"""Single-admin credentials and restart-safe sessions, sharing the existing DB."""
from __future__ import annotations
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from . import store

DEFAULT_SESSION_DAYS = 7
MIN_SESSION_DAYS = 1
MAX_SESSION_DAYS = 30
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**17, 8, 1
SCRYPT_MAXMEM = 256 * 1024 * 1024


class InvalidPassword(ValueError):
    pass


class InvalidSession(ValueError):
    pass


class PasswordPolicyError(ValueError):
    pass


def now():
    return int(time.time())


def lifetime(days):
    return days * 86400


def validate_password(password):
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise PasswordPolicyError('新密码需为 12～256 个字符。')
    try:password.encode('utf-8')
    except UnicodeError:raise PasswordPolicyError('密码包含无法处理的字符。') from None


def password_hash(password):
    validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'),salt=salt,n=SCRYPT_N,r=SCRYPT_R,p=SCRYPT_P,
                            maxmem=SCRYPT_MAXMEM,dklen=32)
    return f'scrypt-v1${salt.hex()}${digest.hex()}'


def password_matches(password, encoded):
    try:
        scheme, salt, expected = encoded.split('$')
        if scheme!='scrypt-v1' or len(salt)!=32 or len(expected)!=64:
            return False
        actual=hashlib.scrypt(password.encode('utf-8'),salt=bytes.fromhex(salt),n=SCRYPT_N,r=SCRYPT_R,p=SCRYPT_P,
                              maxmem=SCRYPT_MAXMEM,dklen=32)
        return hmac.compare_digest(actual,bytes.fromhex(expected))
    except (ValueError,TypeError):
        return False


def init(bootstrap_password):
    with store.LOCK, sqlite3.connect(store.DB) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS admin_account (
                      id INTEGER PRIMARY KEY CHECK(id=1), password_hash TEXT NOT NULL,
                      password_version INTEGER NOT NULL, session_days INTEGER NOT NULL,
                      password_changed_at INTEGER NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS auth_sessions (
                      token_hash TEXT PRIMARY KEY, password_version INTEGER NOT NULL,
                      created_at INTEGER NOT NULL, last_seen INTEGER NOT NULL, expires_at INTEGER NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS auth_sessions_expiry ON auth_sessions(expires_at)')
        exists=db.execute('SELECT 1 FROM admin_account WHERE id=1').fetchone()
    if not exists:
        if not bootstrap_password or bootstrap_password.startswith(('CHANGE_','REPLACE_')):
            raise RuntimeError('首次启动请设置 ADMIN_PASSWORD（至少 12 位）。')
        encoded=password_hash(bootstrap_password)
        with store.LOCK, sqlite3.connect(store.DB) as db:
            db.execute('INSERT OR IGNORE INTO admin_account VALUES (1,?,?,?,?)',
                       (encoded,1,DEFAULT_SESSION_DAYS,now()))
    with store.LOCK, sqlite3.connect(store.DB) as db:
        db.execute('DELETE FROM auth_sessions WHERE expires_at<=?',(now(),))


def token_hash(token):
    if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):
        return None
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def csrf_token(token):
    return hmac.new(token.encode('ascii'),b'ai404-csrf-v1',hashlib.sha256).hexdigest()


def _session(db, digest, timestamp):
    return db.execute('''SELECT s.password_version,s.created_at,s.last_seen,s.expires_at,a.session_days
                         FROM auth_sessions s JOIN admin_account a ON a.id=1 AND a.password_version=s.password_version
                         WHERE s.token_hash=? AND s.expires_at>?''',(digest,timestamp)).fetchone()


def _session_data(token,row):
    return {'password_version':row[0],'created_at':row[1],'last_seen':row[2],'expires_at':row[3],
            'session_days':row[4],'csrf':csrf_token(token)}


def read_session(token):
    digest=token_hash(token)
    if digest is None:return None
    with store.LOCK, sqlite3.connect(store.DB) as db:
        row=_session(db,digest,now())
        return _session_data(token,row) if row else None


def renew_session(token):
    digest=token_hash(token)
    if digest is None:return None
    with store.LOCK, sqlite3.connect(store.DB) as db:
        db.execute('BEGIN IMMEDIATE')
        timestamp=now()
        row=_session(db,digest,timestamp)
        if row is None:return None
        expires=timestamp+lifetime(row[4])
        db.execute('UPDATE auth_sessions SET last_seen=?,expires_at=? WHERE token_hash=?',
                   (timestamp,expires,digest))
        return _session_data(token,(row[0],row[1],timestamp,expires,row[4]))


def verify_password(password):
    with store.LOCK, sqlite3.connect(store.DB) as db:
        row=db.execute('SELECT password_hash,password_version FROM admin_account WHERE id=1').fetchone()
    return row[1] if row and password_matches(password,row[0]) else None


def create_session(password_version, replace_token=''):
    token=secrets.token_urlsafe(32)
    with store.LOCK, sqlite3.connect(store.DB) as db:
        db.execute('BEGIN IMMEDIATE')
        timestamp=now()
        account=db.execute('SELECT password_version,session_days FROM admin_account WHERE id=1').fetchone()
        if account is None or account[0]!=password_version:
            raise InvalidSession('密码已变更，请重新登录。')
        expires=timestamp+lifetime(account[1])
        db.execute('DELETE FROM auth_sessions WHERE expires_at<=? OR token_hash=?',(timestamp,token_hash(replace_token)))
        db.execute('INSERT INTO auth_sessions VALUES (?,?,?,?,?)',(token_hash(token),password_version,timestamp,timestamp,expires))
        return token,_session_data(token,(password_version,timestamp,timestamp,expires,account[1]))


def revoke_session(token):
    with store.LOCK, sqlite3.connect(store.DB) as db:
        db.execute('DELETE FROM auth_sessions WHERE token_hash=?',(token_hash(token),))


def account_settings():
    with store.LOCK, sqlite3.connect(store.DB) as db:
        days,changed=db.execute('SELECT session_days,password_changed_at FROM admin_account WHERE id=1').fetchone()
    return {'session_days':days,'password_changed_at':changed,
            'min_session_days':MIN_SESSION_DAYS,'max_session_days':MAX_SESSION_DAYS,
            'min_password_length':PASSWORD_MIN_LENGTH,'max_password_length':PASSWORD_MAX_LENGTH}


def update_session_days(token, days):
    if type(days) is not int or not MIN_SESSION_DAYS<=days<=MAX_SESSION_DAYS:
        raise ValueError('登录保持时间需为 1～30 天。')
    digest=token_hash(token)
    with store.LOCK, sqlite3.connect(store.DB) as db:
        db.execute('BEGIN IMMEDIATE')
        timestamp=now()
        if not _session(db,digest,timestamp):raise InvalidSession('登录已失效，请重新登录。')
        # Purge first: a longer policy must never resurrect an expired token.
        db.execute('DELETE FROM auth_sessions WHERE expires_at<=?',(timestamp,))
        db.execute('UPDATE admin_account SET session_days=? WHERE id=1',(days,))
        db.execute('UPDATE auth_sessions SET last_seen=? WHERE token_hash=?',(timestamp,digest))
        db.execute('UPDATE auth_sessions SET expires_at=last_seen+?',(lifetime(days),))
        db.execute('DELETE FROM auth_sessions WHERE expires_at<=?',(timestamp,))
    return account_settings()


def change_password(token,current_password,new_password):
    validate_password(new_password)
    version=verify_password(current_password)
    if version is None:raise InvalidPassword('当前密码不正确。')
    if hmac.compare_digest(current_password.encode('utf-8'),new_password.encode('utf-8')):
        raise PasswordPolicyError('新密码需与当前密码不同。')
    encoded=password_hash(new_password)
    with store.LOCK, sqlite3.connect(store.DB) as db:
        db.execute('BEGIN IMMEDIATE')
        row=_session(db,token_hash(token),now())
        if row is None or row[0]!=version:raise InvalidSession('登录已失效，请重新登录。')
        db.execute('UPDATE admin_account SET password_hash=?,password_version=password_version+1,password_changed_at=? WHERE id=1',
                   (encoded,now()))
        db.execute('DELETE FROM auth_sessions')


def reset_password(new_password):
    """Local maintenance only; no HTTP route exposes this operation."""
    encoded=password_hash(new_password)
    with store.LOCK, sqlite3.connect(store.DB) as db:
        changed=db.execute('UPDATE admin_account SET password_hash=?,password_version=password_version+1,password_changed_at=? WHERE id=1',
                           (encoded,now())).rowcount
        if changed!=1:raise RuntimeError('账户尚未初始化。')
        db.execute('DELETE FROM auth_sessions')


if __name__=='__main__':
    import argparse
    import getpass
    parser=argparse.ArgumentParser(description='404 AI 本机账户维护')
    parser.add_argument('action',choices=['reset-password'])
    parser.parse_args()
    password=getpass.getpass('新密码（至少 12 位）：')
    confirmation=getpass.getpass('再次输入：')
    if password!=confirmation:raise SystemExit('两次密码不一致，未修改。')
    reset_password(password)
    print('管理员密码已重置，所有登录会话已失效。')
