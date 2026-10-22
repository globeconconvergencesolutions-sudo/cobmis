-- Catalog (and future SQL-adjacent API paths) need schema USAGE; table SELECTs alone are not enough.
GRANT USAGE ON SCHEMA public_data TO cob_app;
