-- A separate database for `make test-local` and `make migrations-check`: tests refuse
-- to run against a database whose name does not end with _test. Runs on an empty volume only.
CREATE DATABASE app_test;
