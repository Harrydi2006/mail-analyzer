#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
创建管理员账户脚本

使用方法:
python create_admin.py --username admin --email admin@example.com --password your_password
"""

import sys
import argparse
from pathlib import Path
from werkzeug.security import generate_password_hash
import secrets
import string

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent))

from src.core.config import Config
from src.models.database import DatabaseManager, init_database
from src.core.logger import setup_logger, get_logger


def generate_subscribe_key() -> str:
    """生成用户专属的订阅key"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(32))


def create_admin_user(username: str, email: str, password: str) -> bool:
    """创建管理员用户
    
    Args:
        username: 用户名
        email: 邮箱
        password: 密码
        
    Returns:
        是否创建成功
    """
    try:
        # 初始化配置和数据库
        config = Config()
        logger = setup_logger()
        
        # 确保数据库已初始化
        init_database()
        
        db = DatabaseManager(config)
        
        # 检查用户名是否已存在
        existing_user = db.execute_query("SELECT id FROM users WHERE username = ?", (username,))
        if existing_user:
            print(f"❌ 用户名 '{username}' 已存在")
            return False
        
        # 检查邮箱是否已存在
        existing_email = db.execute_query("SELECT id FROM users WHERE email = ?", (email,))
        if existing_email:
            print(f"❌ 邮箱 '{email}' 已被注册")
            return False
        
        # 生成密码哈希和订阅key
        password_hash = generate_password_hash(password)
        subscribe_key = generate_subscribe_key()
        
        # 插入管理员用户
        query = """
        INSERT INTO users (username, email, password_hash, subscribe_key, is_admin, is_active)
        VALUES (?, ?, ?, ?, TRUE, TRUE)
        """
        # 注意：execute_update 返回 rowcount，不是新插入的ID；这里必须用 execute_insert
        user_id = db.execute_insert(query, (username, email, password_hash, subscribe_key))
        
        if user_id:
            print(f"✅ 管理员账户创建成功！")
            print(f"   用户名: {username}")
            print(f"   邮箱: {email}")
            print(f"   用户ID: {user_id}")
            print(f"   订阅Key: {subscribe_key}")
            print(f"\n🔑 请妥善保管登录信息，现在可以使用此账户登录系统")
            return True
        else:
            print("❌ 创建管理员账户失败")
            return False
            
    except Exception as e:
        print(f"❌ 创建管理员账户时发生错误: {e}")
        return False


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='创建管理员账户')
    parser.add_argument('--username', '-u', required=True, help='管理员用户名')
    parser.add_argument('--email', '-e', required=True, help='管理员邮箱')
    parser.add_argument('--password', '-p', required=True, help='管理员密码')
    
    args = parser.parse_args()
    
    # 验证输入
    if len(args.username) < 3:
        print("❌ 用户名长度至少3个字符")
        return False
    
    if '@' not in args.email or '.' not in args.email:
        print("❌ 请输入有效的邮箱地址")
        return False
    
    if len(args.password) < 6:
        print("❌ 密码长度至少6个字符")
        return False
    
    print("🚀 开始创建管理员账户...")
    print(f"   用户名: {args.username}")
    print(f"   邮箱: {args.email}")
    print()
    
    success = create_admin_user(args.username, args.email, args.password)
    
    if success:
        print("\n📋 后续步骤:")
        print("   1. 启动系统: python main.py run")
        print("   2. 访问登录页面: http://127.0.0.1:5000/login")
        print("   3. 使用创建的管理员账户登录")
        print("   4. 访问管理员后台: http://127.0.0.1:5000/admin")
        print("   5. 生成邀请码供其他用户注册")
        return True
    else:
        return False


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)