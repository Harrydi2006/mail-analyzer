/********************************************************************************************************/


文档导
--------
* 集成文档 - https://docs.getui.com/getui/mobile/android/androidstudio/
* API接口文档 - https://docs.getui.com/getui/mobile/android/api/

* 常见问题 - https://docs.getui.com/getui/question/android/
* SDK更新日志 - https://docs.getui.com/getui/version/
* SDK下载链接 - https://docs.getui.com/download.html


==========================================

重点提醒（非常重要）
--------
* Gradle 仅需配置 GETUI_APPID 占位
* 为兼容Android 9.0，务必在application节点添加 android:usesCleartextTraffic="true"(支持http通信，数据已经 RSA + AES + 签名等措施保障安全)；
* 对于同时集成个推多个产品SDK，且SDK之间的APPID值不一致的用户，可以任选一个SDK的APPID配置到GETUI_APPID占位符中，其余SDK在AndroidManifest文件中务必添加对应的标签来补充APPID，详见官网集成文档 2.3 其他说明（重要）
* 请在app/build.gradle 中加入以下代码，使用 java 8.
    compileOptions {
        sourceCompatibility 1.8
        targetCompatibility 1.8
    }
==========================================

个推官网：www.getui.com
个推开放平台：dev.getui.com
客服QQ：2117094763

/********************************************************************************************************/
