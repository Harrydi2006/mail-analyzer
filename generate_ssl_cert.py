#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSL证书生成工具

用于生成自签名SSL证书，供HTTPS开发使用
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    print("错误: 需要安装cryptography库")
    print("请运行: pip install cryptography")
    sys.exit(1)


def generate_self_signed_cert(cert_file="ssl_cert.pem", key_file="ssl_key.pem", 
                              domain="localhost", days=365):
    """
    生成自签名SSL证书
    
    Args:
        cert_file: 证书文件名
        key_file: 私钥文件名
        domain: 域名
        days: 证书有效期（天）
    """
    print(f"正在生成SSL证书...")
    print(f"域名: {domain}")
    print(f"有效期: {days} 天")
    
    # 生成私钥
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    
    # 创建证书主题
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Beijing"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Beijing"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Mail Analyzer"),
        x509.NameAttribute(NameOID.COMMON_NAME, domain),
    ])
    
    # 创建证书
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        private_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.utcnow()
    ).not_valid_after(
        datetime.utcnow() + timedelta(days=days)
    ).add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName(domain),
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]),
        critical=False,
    ).sign(private_key, hashes.SHA256())
    
    # 保存私钥
    with open(key_file, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))
    
    # 保存证书
    with open(cert_file, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    
    print(f"✅ SSL证书生成成功!")
    print(f"   证书文件: {cert_file}")
    print(f"   私钥文件: {key_file}")
    print(f"\n🚀 启动HTTPS服务器:")
    print(f"   python main.py run --ssl --ssl-cert {cert_file} --ssl-key {key_file}")
    print(f"\n⚠️  注意: 这是自签名证书，浏览器会显示安全警告")
    print(f"   生产环境请使用正式的SSL证书")


if __name__ == "__main__":
    import argparse
    import ipaddress
    
    parser = argparse.ArgumentParser(description="生成自签名SSL证书")
    parser.add_argument("--domain", default="localhost", help="域名 (默认: localhost)")
    parser.add_argument("--days", type=int, default=365, help="有效期天数 (默认: 365)")
    parser.add_argument("--cert", default="ssl_cert.pem", help="证书文件名")
    parser.add_argument("--key", default="ssl_key.pem", help="私钥文件名")
    
    args = parser.parse_args()
    
    try:
        generate_self_signed_cert(
            cert_file=args.cert,
            key_file=args.key,
            domain=args.domain,
            days=args.days
        )
    except Exception as e:
        print(f"❌ 证书生成失败: {e}")
        sys.exit(1)