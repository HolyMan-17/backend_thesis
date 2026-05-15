#!/usr/bin/env python3
"""
Seed a test device and link it to a user.

Usage:
    python scripts/seed_test_device.py --mac 00:1B:44:11:3A:B7 --auth0-id auth0|64f8a1b2c3d4e5f6a7b8c9d0
    python scripts/seed_test_device.py --mac 00:1B:44:11:3A:B7 --auth0-id auth0|64f8a1b2c3d4e5f6a7b8c9d0 --name "Kitchen Light" --prioridad alta --limite 150
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

import pymysql


def main():
    parser = argparse.ArgumentParser(description="Seed test device + user permission")
    parser.add_argument("--mac", required=True, help="Device MAC address (AA:BB:CC:DD:EE:FF)")
    parser.add_argument("--auth0-id", required=True, help="Auth0 user ID (sub claim)")
    parser.add_argument("--name", default=None, help="Custom device name")
    parser.add_argument("--prioridad", default="media", choices=["alta", "media", "baja"], help="Priority level")
    parser.add_argument("--limite", type=float, default=150.0, help="Power limit in watts")
    args = parser.parse_args()

    db_host = os.getenv("DB_HOST", "127.0.0.1")
    db_port = int(os.getenv("DB_PORT", "3306"))
    db_user = os.getenv("DB_USER")
    db_password = os.getenv("DB_PASSWORD")
    db_name = os.getenv("DB_NAME")

    if not all([db_user, db_password, db_name]):
        print("ERROR: DB_USER, DB_PASSWORD, DB_NAME must be set in .env")
        sys.exit(1)

    conn = pymysql.connect(
        host=db_host,
        port=db_port,
        user=db_user,
        password=db_password,
        database=db_name,
        charset="utf8mb4",
        autocommit=True,
    )

    try:
        with conn.cursor() as cur:
            # 1. Upsert artefacto
            cur.execute(
                """
                INSERT INTO artefactos (mac, nombre_personalizado, nivel_prioridad, limite_consumo_w, is_online, is_encendido)
                VALUES (%s, %s, %s, %s, FALSE, FALSE)
                ON DUPLICATE KEY UPDATE mac = mac
                """,
                (args.mac, args.name, args.prioridad, args.limite),
            )
            print(f"[OK] Artefacto upserted: {args.mac}")

            # 2. Get artefacto ID
            cur.execute("SELECT id FROM artefactos WHERE mac = %s", (args.mac,))
            row = cur.fetchone()
            if not row:
                print(f"ERROR: Artefacto not found after upsert: {args.mac}")
                sys.exit(1)
            artefacto_id = row[0]
            print(f"[OK] Artefacto ID: {artefacto_id}")

            # 3. Find user by auth0_id
            cur.execute("SELECT id FROM usuarios WHERE auth0_id = %s AND activo = TRUE", (args.auth0_id,))
            row = cur.fetchone()
            if not row:
                print(f"ERROR: User not found with auth0_id={args.auth0_id}")
                print("       Run POST /api/users/sync first, or create user manually.")
                sys.exit(1)
            user_id = row[0]
            print(f"[OK] User ID: {user_id}")

            # 4. Upsert permission
            cur.execute(
                """
                INSERT INTO permisos_usuario_artefacto (id_usuario, id_artefacto, nivel_acceso)
                VALUES (%s, %s, 'ADMIN')
                ON DUPLICATE KEY UPDATE nivel_acceso = 'ADMIN'
                """,
                (user_id, artefacto_id),
            )
            print(f"[OK] Permission granted: user {user_id} -> device {artefacto_id} (ADMIN)")

            print(f"\nDone. Device {args.mac} is now accessible by user {args.auth0_id}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()