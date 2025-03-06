# 视频下载工具

一个简单的视频下载工具，支持多种视频网站。

## 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 运行开发服务器
uvicorn main:app --reload
```

## Vercel 部署

直接使用 GitHub 导入项目到 Vercel 即可。

## 注意事项

- 临时文件存储在 /tmp 目录
- 文件大小限制为 50MB
- 下载完成后请及时保存文件