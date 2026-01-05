#!/usr/bin/env python3
"""
测试登录状态检测功能
"""

import requests
import time
import json

def test_auth_check():
    """测试登录状态检测API"""
    base_url = "http://localhost:5000"
    
    print("🔍 测试登录状态检测功能")
    print("=" * 50)
    
    # 1. 测试未登录状态
    print("\n1. 测试未登录状态...")
    try:
        response = requests.get(f"{base_url}/api/auth/check", timeout=5)
        print(f"   状态码: {response.status_code}")
        if response.status_code == 401:
            print("   ✅ 未登录状态检测正常")
        else:
            print("   ❌ 未登录状态检测异常")
    except Exception as e:
        print(f"   ❌ 请求失败: {e}")
    
    # 2. 测试登录后的状态检测
    print("\n2. 测试登录后状态检测...")
    session = requests.Session()
    
    # 尝试登录（需要有效的用户名和密码）
    login_data = {
        "username": "admin",  # 请根据实际情况修改
        "password": "admin123"  # 请根据实际情况修改
    }
    
    try:
        login_response = session.post(f"{base_url}/login", data=login_data, timeout=5)
        print(f"   登录状态码: {login_response.status_code}")
        
        if login_response.status_code == 200:
            # 测试登录状态检测
            auth_response = session.get(f"{base_url}/api/auth/check", timeout=5)
            print(f"   认证状态码: {auth_response.status_code}")
            
            if auth_response.status_code == 200:
                data = auth_response.json()
                if data.get('success') and data.get('authenticated'):
                    print("   ✅ 登录状态检测正常")
                    print(f"   用户信息: {data.get('user', {}).get('username', 'N/A')}")
                else:
                    print("   ❌ 登录状态检测异常")
            else:
                print("   ❌ 认证请求失败")
        else:
            print("   ❌ 登录失败，请检查用户名和密码")
            
    except Exception as e:
        print(f"   ❌ 请求失败: {e}")
    
    # 3. 测试用户信息API
    print("\n3. 测试用户信息API...")
    try:
        profile_response = session.get(f"{base_url}/api/user/profile", timeout=5)
        print(f"   用户信息状态码: {profile_response.status_code}")
        
        if profile_response.status_code == 200:
            data = profile_response.json()
            if data.get('success'):
                print("   ✅ 用户信息获取正常")
                print(f"   用户: {data.get('user', {}).get('username', 'N/A')}")
            else:
                print("   ❌ 用户信息获取异常")
        else:
            print("   ❌ 用户信息请求失败")
            
    except Exception as e:
        print(f"   ❌ 请求失败: {e}")
    
    print("\n" + "=" * 50)
    print("🎯 测试完成")

if __name__ == "__main__":
    test_auth_check()
