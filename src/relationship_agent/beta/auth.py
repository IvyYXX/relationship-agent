"""账号认证与短期会话；只保存密码派生值和令牌摘要。"""

import hashlib
import hmac
import secrets
import re
from sqlalchemy import select, insert, delete, update
from .database import users, tokens, quota, ident, stamp


class AccessError(ValueError):
    """认证、邀请码或频率限制失败；信息可以安全地反馈给用户。"""

    pass


def digest(value):
    """生成会话令牌和限流标识的摘要；不能用来替代密码哈希。"""
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(value):
    """用随机盐和 scrypt 派生密码哈希，返回可保存的盐与摘要。"""
    salt = secrets.token_bytes(16)
    return (
        salt.hex()
        + ":"
        + hashlib.scrypt(value.encode(), salt=salt, n=16384, r=8, p=1).hex()
    )


def verify(value, stored):
    """以恒定时间比较校验密码；损坏的哈希视为验证失败。"""
    try:
        salt, expected = stored.split(":")
        actual = hashlib.scrypt(
            value.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1
        ).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class Auth:
    """管理密码、短期登录令牌和共享数据库中的登录频率限制。"""

    def __init__(self, db, settings):
        """绑定数据库和配置，并准备不存在账号时使用的等成本密码校验。"""
        self.db, self.settings = db, settings
        self.dummy = password_hash("dummy-timing-password")

    def throttle(self, ip, username):
        """按 IP 和用户名累计十分钟窗口内的尝试，超限抛出 AccessError。"""
        minute = int(stamp() // 600)
        with self.db.tx() as c:
            for key, cap in [
                (f"auth-ip:{digest(ip)}:{minute}", 30),
                (f"auth-user:{digest(username)}:{minute}", 10),
            ]:
                row = (
                    c.execute(select(quota).where(quota.c.key == key))
                    .mappings()
                    .first()
                )
                if row and row["calls"] >= cap:
                    raise AccessError("尝试过多，请十分钟后重试 / Too many attempts")
                if row:
                    c.execute(
                        update(quota)
                        .where(quota.c.key == key)
                        .values(calls=row["calls"] + 1)
                    )
                else:
                    c.execute(insert(quota).values(key=key, calls=1, reserved_usd=0))

    def login(
        self,
        username,
        password,
        *,
        register=False,
        invite="",
        consent=False,
        adult=False,
    ):
        """验证登录或注册条件，持久化会话摘要并返回原始 token 与 CSRF 值。"""
        username = username.strip().lower()
        if (
            not re.fullmatch(r"[a-z0-9_\-]{3,40}", username)
            or not 12 <= len(password) <= 128
        ):
            raise AccessError(
                "用户名需3–40位字母/数字，密码需12–128位 / Invalid username or password length"
            )
        if register:
            if not consent or not adult:
                raise AccessError(
                    "请确认年满18岁并阅读数据说明 / Adult consent required"
                )
            if self.settings.invite_code and not hmac.compare_digest(
                invite, self.settings.invite_code
            ):
                raise AccessError("邀请码无效 / Invalid invitation")
            hashed = password_hash(password)
            with self.db.tx() as c:
                if c.execute(
                    select(users.c.id).where(users.c.username == username)
                ).first():
                    raise AccessError("无法注册该用户名 / Username unavailable")
                uid = ident()
                c.execute(
                    insert(users).values(
                        id=uid,
                        username=username,
                        password_hash=hashed,
                        created=stamp(),
                        epoch=0,
                        consent_version="2026-10-v1",
                    )
                )
        else:
            with self.db.read() as c:
                row = (
                    c.execute(select(users).where(users.c.username == username))
                    .mappings()
                    .first()
                )
            if (
                not verify(password, row["password_hash"] if row else self.dummy)
                or not row
            ):
                raise AccessError("用户名或密码错误 / Invalid credentials")
            uid = row["id"]
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.db.tx() as c:
            c.execute(delete(tokens).where(tokens.c.expires < stamp()))
            if not c.execute(select(users.c.id).where(users.c.id == uid)).first():
                raise AccessError("Account unavailable")
            c.execute(
                insert(tokens).values(
                    digest=digest(token),
                    user_id=uid,
                    csrf=csrf,
                    expires=stamp() + 7 * 86400,
                )
            )
        return token, csrf

    def authenticate(self, token):
        """校验未过期会话，返回账号身份及 CSRF 值；不返回密码哈希。"""
        with self.db.read() as c:
            row = (
                c.execute(
                    select(tokens.c.user_id, tokens.c.csrf, users.c.username)
                    .join(users, users.c.id == tokens.c.user_id)
                    .where(tokens.c.digest == digest(token), tokens.c.expires > stamp())
                )
                .mappings()
                .first()
            )
        if not row:
            raise AccessError("请登录 / Please sign in")
        return dict(row)

    def logout(self, token):
        """撤销当前令牌，不影响该用户的其他登录会话。"""
        with self.db.tx() as c:
            c.execute(delete(tokens).where(tokens.c.digest == digest(token)))
