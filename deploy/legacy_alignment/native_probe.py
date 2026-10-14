"""Read-only local WSL metadata probe; never print database credentials or rows."""

import argparse
import hashlib
import json
import os
from pathlib import Path

import pymysql


TARGETS = ("Jifeng Inventory Profile", "Jifeng Inventory Dictionary", "Jifeng Inventory Category Batch")


def probe(config_path):
    common = config_path.parent.parent / "common_site_config.json"
    configuration = {**(json.loads(common.read_text()) if common.exists() else {}),
                     **json.loads(config_path.read_text())}
    host = configuration.get("db_host", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("Only the existing local WSL database is allowed")
    socket = configuration.get("db_socket")
    if socket and Path(socket).resolve() != Path("/run/mysqld/mysqld.sock"):
        raise ValueError("Unexpected local database socket")
    database = configuration["db_name"]
    connection = pymysql.connect(host=host, port=int(configuration.get("db_port", 3306)), unix_socket=socket,
        user=configuration.get("db_user", database), password=configuration["db_password"],
        database=database, charset="utf8mb4", autocommit=False,
        cursorclass=pymysql.cursors.DictCursor, connect_timeout=8, read_timeout=30)
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            cursor.execute("SELECT VERSION() AS version")
            result = {"database_identity_sha256": hashlib.sha256(database.encode()).hexdigest(),
                      "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
                      "server": cursor.fetchone()["version"], "targets": {}}
            cursor.execute("SELECT COUNT(*) AS tables, SUM(data_length+index_length) AS bytes "
                           "FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()")
            result["size"] = cursor.fetchone()
            for doctype in TARGETS:
                cursor.execute("SELECT name,module,track_changes,is_submittable FROM `tabDocType` WHERE name=%s", (doctype,))
                header = cursor.fetchone()
                if not header:
                    result["targets"][doctype] = {"installed": False}
                    continue
                cursor.execute("SELECT fieldname,fieldtype,options,`default`,read_only,hidden,`unique`,idx "
                               "FROM `tabDocField` WHERE parent=%s ORDER BY idx,name", (doctype,))
                fields = cursor.fetchall()
                cursor.execute("SELECT * FROM `tabDocPerm` WHERE parent=%s ORDER BY idx,name", (doctype,))
                permissions = cursor.fetchall()
                cursor.execute("SELECT * FROM `tabCustom DocPerm` WHERE parent=%s ORDER BY idx,name", (doctype,))
                overrides = cursor.fetchall()
                table = "tab" + doctype
                cursor.execute("SHOW CREATE TABLE `" + table + "`")
                definition = cursor.fetchone()["Create Table"]
                cursor.execute("SELECT COUNT(*) AS row_count FROM `" + table + "`")
                result["targets"][doctype] = {"installed": True, "header": header, "fields": fields,
                    "permissions": permissions, "custom_permissions": overrides,
                    "rows": cursor.fetchone()["row_count"], "schema": definition}
            cursor.execute("SELECT name FROM `tabCompany` ORDER BY name")
            result["company_count"] = len(cursor.fetchall())
            cursor.execute("SELECT COUNT(*) AS row_count FROM `tabItem`")
            result["item_count"] = cursor.fetchone()["row_count"]
            return result
    finally:
        connection.rollback()
        connection.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = probe(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"read_only": True, "tables": result["size"]["tables"],
        "database_bytes": result["size"]["bytes"], "company_count": result["company_count"],
        "item_count": result["item_count"],
        "targets": {name: {"installed": value["installed"], "rows": value.get("rows"),
                           "fields": len(value.get("fields", []))} for name, value in result["targets"].items()}}, default=str))


if __name__ == "__main__":
    main()
