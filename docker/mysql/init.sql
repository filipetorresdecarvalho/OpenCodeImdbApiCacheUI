CREATE DATABASE IF NOT EXISTS imdb_cache CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'imdb_user'@'%' IDENTIFIED BY 'rootpassword';
GRANT ALL PRIVILEGES ON imdb_cache.* TO 'imdb_user'@'%';
FLUSH PRIVILEGES;
