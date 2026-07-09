-- MySQL 8.x schema for the medical report interpretation agent.
-- The running demo uses SQLite by default; this DDL is the production-ready
-- relational model for MySQL deployment.

CREATE TABLE users (
  user_id VARCHAR(32) PRIMARY KEY,
  name VARCHAR(80) NOT NULL,
  sex VARCHAR(16),
  age INT,
  phone VARCHAR(32),
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uq_user_name_phone (name, phone)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE user_profiles (
  user_id VARCHAR(32) PRIMARY KEY,
  chronic_diseases TEXT,
  medications TEXT,
  allergies TEXT,
  family_history TEXT,
  pregnancy_status VARCHAR(80),
  lifestyle TEXT,
  risk_preferences TEXT,
  json_profile JSON,
  updated_at DATETIME NOT NULL,
  CONSTRAINT fk_profile_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE report_cases (
  task_id VARCHAR(32) PRIMARY KEY,
  user_id VARCHAR(32),
  created_at DATETIME NOT NULL,
  raw_text MEDIUMTEXT,
  source_type VARCHAR(32),
  risk_level VARCHAR(16),
  risk_score INT,
  requires_review TINYINT(1),
  safety_notice TEXT,
  json_result JSON NOT NULL,
  KEY idx_cases_user_time (user_id, created_at),
  CONSTRAINT fk_case_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE observations (
  obs_id VARCHAR(32) PRIMARY KEY,
  task_id VARCHAR(32) NOT NULL,
  code VARCHAR(32),
  name VARCHAR(128),
  value DECIMAL(12, 4),
  unit VARCHAR(32),
  status VARCHAR(16),
  severity INT,
  reference_range VARCHAR(128),
  standard_id VARCHAR(64),
  source TEXT,
  KEY idx_observations_code (code),
  CONSTRAINT fk_obs_case FOREIGN KEY (task_id) REFERENCES report_cases(task_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE review_tasks (
  review_id VARCHAR(32) PRIMARY KEY,
  task_id VARCHAR(32) NOT NULL,
  status VARCHAR(64),
  risk_level VARCHAR(16),
  reason TEXT,
  doctor_review_json JSON,
  created_at DATETIME NOT NULL,
  updated_at DATETIME,
  CONSTRAINT fk_review_case FOREIGN KEY (task_id) REFERENCES report_cases(task_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE followups (
  follow_id VARCHAR(32) PRIMARY KEY,
  task_id VARCHAR(32) NOT NULL,
  owner VARCHAR(80),
  title TEXT,
  priority VARCHAR(16),
  due_date DATE,
  status VARCHAR(64),
  questions_json JSON,
  feedback_json JSON,
  created_at DATETIME NOT NULL,
  updated_at DATETIME,
  KEY idx_followups_due (due_date, status),
  CONSTRAINT fk_followup_case FOREIGN KEY (task_id) REFERENCES report_cases(task_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE audit_events (
  event_id VARCHAR(32) PRIMARY KEY,
  time DATETIME NOT NULL,
  actor VARCHAR(80),
  action VARCHAR(128),
  target VARCHAR(64),
  detail TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE conversation_turns (
  turn_id VARCHAR(32) PRIMARY KEY,
  user_id VARCHAR(32),
  role VARCHAR(16) NOT NULL,
  content TEXT NOT NULL,
  metadata_json JSON,
  created_at DATETIME NOT NULL,
  CONSTRAINT fk_turn_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
