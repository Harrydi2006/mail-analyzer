#!/usr/bin/env python3
"""
简单测试登录状态检测功能
"""

import requests
import json

def test_auth_status():
    """测试登录状态检测"""
    base_url = "http://localhost:5000"
    
    print("🔍 测试登录状态检测功能")
    print("=" * 40)
    
    # 测试未登录状态
    print("\n1. 测试未登录状态...")
    try:
        response = requests.get(f"{base_url}/api/auth/check", timeout=5)
        print(f"   状态码: {response.status_code}")
        
        if response.status_code == 401:
            data = response.json()
            print(f"   响应: {data}")
            print("   ✅ 未登录状态检测正常")
        else:
            print("   ❌ 未登录状态检测异常")
            
    except Exception as e:
        print(f"   ❌ 请求失败: {e}")
    
    print("\n" + "=" * 40)
    print("🎯 测试完成")

if __name__ == "__main__":
    test_auth_status()
