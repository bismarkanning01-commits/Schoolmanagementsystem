CREATE DATABASE school_db;
USE school_db;

DROP TABLE IF EXISTS teacher_class;
DROP TABLE IF EXISTS performances;

-- =========================
-- USERS
-- =========================
CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL,
    school_id INT
);

-- =========================
-- SCHOOLS
-- =========================
CREATE TABLE schools (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(150),
    address VARCHAR(200),
    subscription_status VARCHAR(50),
    plan VARCHAR(50),
    created_at DATETIME
);

-- =========================
-- CLASSES
-- =========================
CREATE TABLE classes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    school_id INT,
    FOREIGN KEY (school_id) REFERENCES schools(id)
);

-- =========================
-- TEACHER-CLASS (IMPORTANT 🔥)
-- =========================
CREATE TABLE teacher_class (
    teacher_id INT,
    class_id INT,
    PRIMARY KEY (teacher_id, class_id),

    FOREIGN KEY (teacher_id) REFERENCES users(id),
    FOREIGN KEY (class_id) REFERENCES classes(id)
);

-- =========================
-- STUDENTS
-- =========================
CREATE TABLE students (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100),
    subject VARCHAR(100),
    phone VARCHAR(20),
    class_id INT NOT NULL,
    created_at DATETIME,
    school_id INT,

    FOREIGN KEY (class_id) REFERENCES classes(id),
    FOREIGN KEY (school_id) REFERENCES schools(id)
);

ALTER TABLE students
ADD COLUMN fee_amount DECIMAL(10,2) DEFAULT 0;

-- =========================
-- ATTENDANCE
-- =========================
CREATE TABLE attendance (
    id INT AUTO_INCREMENT PRIMARY KEY,
    student_id INT,
    status VARCHAR(10),
    date DATE,
    school_id INT,

    FOREIGN KEY (student_id) REFERENCES students(id)
);

-- =========================
-- INVOICES
-- =========================
CREATE TABLE invoices (
    id INT AUTO_INCREMENT PRIMARY KEY,
    student_id INT,
    amount FLOAT,
    status VARCHAR(20),
    date_created DATETIME,
    partial_paid FLOAT,
    term VARCHAR(50),
    academic_year VARCHAR(20),
    tuition_fee DECIMAL(10,2) NOT NULL DEFAULT 0,
    sports_fee DECIMAL(10,2) NOT NULL DEFAULT 0,
    library_fee DECIMAL(10,2) NOT NULL DEFAULT 0,
    ict_fee DECIMAL(10,2) NOT NULL DEFAULT 0,
    school_id INT,

    FOREIGN KEY (student_id) REFERENCES students(id)
);

-- The application adds these columns at startup for existing databases.
-- If applying the migration manually, run this only; do not rerun this schema
-- file, which contains other data-reset statements.
-- ALTER TABLE invoices
--     ADD COLUMN term VARCHAR(50) NULL,
--     ADD COLUMN academic_year VARCHAR(20) NULL,
--     ADD COLUMN tuition_fee DECIMAL(10,2) NOT NULL DEFAULT 0,
--     ADD COLUMN sports_fee DECIMAL(10,2) NOT NULL DEFAULT 0,
--     ADD COLUMN library_fee DECIMAL(10,2) NOT NULL DEFAULT 0,
--     ADD COLUMN ict_fee DECIMAL(10,2) NOT NULL DEFAULT 0;

-- =========================
-- PERFORMANCE
-- =========================
CREATE TABLE performances (
    id INT AUTO_INCREMENT PRIMARY KEY,
    student_id INT NOT NULL,
    subject VARCHAR(100),
    class_score FLOAT DEFAULT 0,
    exam_score FLOAT DEFAULT 0,
    total_score FLOAT DEFAULT 0,
    term VARCHAR(20),
    academic_year VARCHAR(20),
    date DATETIME DEFAULT CURRENT_TIMESTAMP,
    school_id INT,
    FOREIGN KEY (student_id) REFERENCES students(id),
    FOREIGN KEY (school_id) REFERENCES schools(id)
);

ALTER TABLE performances
ADD COLUMN class_score FLOAT DEFAULT 0;

ALTER TABLE performances
ADD COLUMN exam_score FLOAT DEFAULT 0;

ALTER TABLE performances
ADD COLUMN total_score FLOAT DEFAULT 0;
SET FOREIGN_KEY_CHECKS = 0;
UPDATE performances
SET total_score = class_score + exam_score;

TRUNCATE TABLE teacher_class;
TRUNCATE TABLE attendance;
TRUNCATE TABLE performances;
TRUNCATE TABLE invoices;
TRUNCATE TABLE students;
TRUNCATE TABLE classes;
TRUNCATE TABLE users;
TRUNCATE TABLE schools;

SET FOREIGN_KEY_CHECKS = 1;
SELECT * FROM users;
DELETE FROM users WHERE id BETWEEN 1 AND 9;
SELECT * FROM users;

ALTER TABLE users
ADD COLUMN reset_token VARCHAR(200);

CREATE TABLE fee_structure (
    id INT AUTO_INCREMENT PRIMARY KEY,
    class_id INT NOT NULL,
    amount DECIMAL(10,2) NOT NULL,
    term VARCHAR(50),
    academic_year VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS term_fee_schedules (
    id INT AUTO_INCREMENT PRIMARY KEY,
    class_id INT NOT NULL,
    term VARCHAR(50) NOT NULL,
    academic_year VARCHAR(20) NOT NULL,
    tuition_fee FLOAT NOT NULL DEFAULT 0,
    sports_fee FLOAT NOT NULL DEFAULT 0,
    library_fee FLOAT NOT NULL DEFAULT 0,
    ict_fee FLOAT NOT NULL DEFAULT 0,
    CONSTRAINT uq_term_fee_schedule_class_term_year
        UNIQUE (class_id, term, academic_year),
    CONSTRAINT fk_term_fee_schedules_class
        FOREIGN KEY (class_id) REFERENCES classes(id)
);

CREATE TABLE payments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    student_id INT,
    amount DECIMAL(10,2),
    method VARCHAR(50),
    payment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE payments
ADD COLUMN invoice_id INT;

CREATE TABLE payments (
    id INT AUTO_INCREMENT PRIMARY KEY,

    student_id INT NOT NULL,
    invoice_id INT NOT NULL,

    amount_paid DECIMAL(10,2) NOT NULL,

    method VARCHAR(50) NOT NULL DEFAULT 'Cash',

    reference VARCHAR(100) NULL,

    payment_date DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    remarks TEXT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (student_id)
        REFERENCES students(id)
        ON DELETE CASCADE,

    FOREIGN KEY (invoice_id)
        REFERENCES invoices(id)
        ON DELETE CASCADE
);
DESCRIBE payments;
DROP TABLE IF EXISTS payments;
ALTER TABLE users
DROP COLUMN school_id;

SELECT
    student_id,
    subject,
    term,
    academic_year,
    total_score
FROM performances
WHERE student_id = 1
ORDER BY subject;

SELECT *
FROM performances
ORDER BY student_id, subject;