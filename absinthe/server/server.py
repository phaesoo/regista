import os
import logging
import socket
import time
import pickle
from threading import Thread, Lock
from .define import define
from . import schedule
from absinthe.configs.util import get_defined_path
from absinthe.external.daemon import Daemon
from absinthe.tasks.tasks import get_app
from absinthe.utils.rabbitmq import RabbitMQClient
from absinthe.utils.mysql import MySQLClient


logger = logging.getLogger("server")


class Server(Daemon):
    def __init__(self, configs):
        assert isinstance(configs, dict)

        self._config_common = configs["services"]["common"]
        self._config_server = configs["services"]["server"]

        log_path = get_defined_path(configs["services"]["server"]["log_path"], configs)
        pidfile = os.path.join(log_path, "server.pid")
        daemon_log = os.path.join(log_path, "daemon.log")

        super().__init__(
            pidfile=pidfile,
            stdout=daemon_log,
            stderr=daemon_log
        )

        self._app = get_app(**self._config_common["celery"])

        self._conn = MySQLClient()
        self._conn.init(**self._config_common["mysql"])

        self._interval = self._config_server["interval"]
        self._status = define.STATUS_RUNNING if self._config_server[
            "auto_start"] else define.STATUS_STOPPED

        self._mutex = Lock()

    def run(self):
        logger.info("Server has been started")
        threads = [Thread(target=self._wrap, args=[func])
                   for func in [self._communicate, self._update_result]]

        for t in threads:
            t.start()

        try:
            self._wrap(self._main)
        except:
            pass

        for t in threads:
            t.join()
        logger.info("Server has been terminated")

    def _set_status(self, status):
        with self._mutex:
            self._status = status

    def _wrap(self, func, *args, **kwargs):
        try:
            func(*args, **kwargs)
        except Exception as e:
            self._status = define.STATUS_TERMINATED
            logger.critical(f"Unexpected error. terminate server :{e}")

    def _communicate(self):
        """
        thread for communicating with external clients(control)
        """
        logger.debug("communicate thread has been started")

        # prepare for server_socket
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(
            (self._config_server["host"], self._config_server["port"]))
        server_socket.listen()
        server_socket.settimeout(0.5)

        while True:
            if self._status == define.STATUS_TERMINATED:
                logger.debug("communicate thread has been terminated")
                break
            try:
                client_socket, _ = server_socket.accept()
                msg = client_socket.recv(1024).decode()
                if msg == "hi":
                    client_socket.sendall("hello".encode())
                elif msg == "status":
                    client_socket.sendall(self._status.encode())
                else:
                    logger.warn(f"Unknown msg: {msg}")
            except socket.timeout:
                pass
            except Exception as e:
                logger.error(f"health_check error: {e}")

        client_socket.close()
        server_socket.close()

    def _main(self):
        """
        main thread for handling message queues and assigning jobs
        """
        logger.debug("main thread has been started")

        mq_client = RabbitMQClient()
        mq_client.init(**self._config_common["rabbitmq"])

        queue = self._config_common["rabbitmq"]["queue"]
        # purge and declare before starting
        try:
            pass
            #mq_client.queue_purge(queue)
        except:
            pass
        mq_client.queue_declare(queue)

        is_first = True

        while True:
            if self._status == define.STATUS_TERMINATED:
                logger.debug("main thread has been terminated")
                break

            # imte interval from second loop
            if is_first is True:
                is_first = False
            else:
                time.sleep(self._interval)

            # main queue has high priority
            data = mq_client.get(queue)
            if data:
                logger.debug(data)
                try:
                    self._handle_queue(data)
                except Exception as e:
                    logger.error(f"Error while _handle_queue: {e}")
                continue

            if self._status == define.STATUS_STOPPED:
                logger.debug("Server has been stopped")
                continue

            # assign jobs
            self._assign_jobs()

    def _handle_queue(self, data):
        title = data["title"]
        body = data["body"]

        if title == "server":
            cmd = body.get("command", None)
            by = body.get("by", "undefined")
            if cmd == "terminate":
                logger.info(f"Server is terminated by {by}")
                self._set_status(define.STATUS_TERMINATED)
            elif cmd == "stop":
                self._set_status(define.STATUS_STOPPED)
                logger.info(f"Server is stopped by {by}")
            elif cmd == "resume":
                self._set_status(define.STATUS_RUNNING)
                logger.info(f"Server is resumed by {by}")
            else:
                logger.info(f"Undefined {title} command {by}: {cmd}")
        elif title == "schedule":
            cmd = body.get("command", None)
            if cmd == "insert":
                date = body["date"]
                assert isinstance(date, str)
                try:
                    schedule.dump_schedule_hist(self._conn)
                    schedule.generate_schedule(self._conn, date)
                    self._conn.commit()
                except Exception as e:
                    logger.error(e)
                    self._conn.rollback()
            else:
                logger.warn(f"Undefined {title} command {by}: {cmd}")
        else:
            raise ValueError(f"Undefined title: {title}")

    def _assign_jobs(self):
        # assign jobs
        jobs = schedule.get_assignable_jobs(self._conn)
        if not len(jobs):
            logger.debug("There is no assignable jobs")
            return

        try:
            for row in jobs:
                logger.debug(f"assign job: {row[1]}")
                task_id = self._app.send_task("script", [row[1]])
                self._conn.execute(
                    f"""
                    update job_schedule set job_status=1, task_id='{task_id}', run_count=run_count+1 where jid={row[0]};
                    """
                )
            self._conn.commit()
        except Exception as e:
            logger.error(e)
            self._conn.rollback()

    def _update_result(self):
        """
        thread for updating result
        """
        logger.debug("update_result thread has been started")

        is_first = True

        while True:
            # imte interval from second loop
            if is_first is True:
                is_first = False
            else:
                time.sleep(self._interval)

            if self._status == define.STATUS_TERMINATED:
                logger.debug("update_result thread has been terminated")
                break
            elif self._status == define.STATUS_STOPPED:
                logger.debug("update_result thread has been stopped")
                continue

            # get finished jobs
            data = self._conn.fetchall(
                """
                SELECT task_id, jid from job_schedule where task_id IS NOT NULL;
                """
            )
            if not len(data):
                logger.debug("No data to update result")
                continue

            # update job_status and task_id=NULL
            try:
                for row in data:
                    result = self._app.AsyncResult(row[0])
                    print(result.state)
                    if result.state == "PENDING":
                        self._conn.execute(
                            f"""
                            UPDATE job_schedule SET job_status=-999, task_id=NULL where jid={row[1]};
                            """
                        )
                    elif result.ready():
                        result_code = result.get()
                        if result_code == 0:
                            result_code = 99
                        else:
                            result_code = -result_code
                        self._conn.execute(
                            f"""
                            update job_schedule set job_status={result_code}, task_id=NULL where jid={row[1]};
                            """
                        )
                self._conn.commit()
            except Exception as e:
                logger.error(e)
                self._conn.rollback()