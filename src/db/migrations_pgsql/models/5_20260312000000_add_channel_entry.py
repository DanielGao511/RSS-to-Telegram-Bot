#  RSS to Telegram Bot
#  Copyright (C) 2026  Rongrong <i@rong.moe>
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU Affero General Public License as
#  published by the Free Software Foundation, either version 3 of the
#  License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU Affero General Public License for more details.
#
#  You should have received a copy of the GNU Affero General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "user" ADD "summary_enabled" BOOL NOT NULL DEFAULT TRUE;
        ALTER TABLE "user" ADD "summary_interval" SMALLINT NOT NULL DEFAULT 480;
        ALTER TABLE "user" ADD "summary_at" VARCHAR(5) NOT NULL DEFAULT '06:00';
        ALTER TABLE "user" ADD "summary_pin" BOOL NOT NULL DEFAULT TRUE;
        ALTER TABLE "user" ADD "summary_msg_ids" JSON;
        CREATE TABLE IF NOT EXISTS "channel_entry" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "user_id" BIGINT NOT NULL,
            "feed_id" INT NOT NULL,
            "title" TEXT NOT NULL,
            "link" VARCHAR(4096) NOT NULL,
            "content" TEXT NOT NULL,
            "published_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
            "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS "idx_channel_ent_user_id_478061" ON "channel_entry" ("user_id");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "channel_entry";"""
