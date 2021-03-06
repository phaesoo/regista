CREATE DATABASE `nesta`;

USE `nesta`;

DROP TABLE IF EXISTS `job`;
CREATE TABLE `job` (
    `jid` INT PRIMARY KEY,
    `job_name` VARCHAR(40) UNIQUE,
    `job_type` VARCHAR(10),
    `queue` VARCHAR(20),
    `author` VARCHAR(10),
    `cron` VARCHAR(30),
    `max_run_count` INT
);


DROP TABLE IF EXISTS `job_schedule`;
CREATE TABLE `job_schedule` (
    `jid` INT PRIMARY KEY,
    `job_date` DATE,
    `job_status` INT DEFAULT 0,
    `scheduled_time` DATETIME DEFAULT NULL,
    `assign_time` DATETIME DEFAULT NULL,
    `start_time` DATETIME DEFAULT NULL,
    `end_time` DATETIME DEFAULT NULL,
    `run_count` INT DEFAULT 0,
    `max_run_count` INT,
    `task_id` CHAR(36) DEFAULT NULL
);


DROP TABLE IF EXISTS `job_schedule_hist`;
CREATE TABLE job_schedule_hist (
    `id` INT PRIMARY KEY AUTO_INCREMENT,
    `jid` INT,
    `job_date` DATE,
    `job_status` INT,
    `assign_time` DATETIME,
    `start_time` DATETIME,
    `end_time` DATETIME,
    `run_count` INT
);


DROP TABLE IF EXISTS `job_dependency`;
CREATE TABLE job_dependency (
    `id` INT PRIMARY KEY AUTO_INCREMENT,
    `jid` INT,
    `dependent_jid` INT
);