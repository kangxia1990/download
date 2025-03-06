from fastapi import FastAPI, Request, BackgroundTasks, Form
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, RedirectResponse
import yt_dlp
import os
import asyncio
from pathlib import Path
import re
import boto3
from botocore.exceptions import ClientError
from functools import lru_cache
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 配置静态文件和模板，使用相对路径
app.mount("/static", StaticFiles(directory="./static"), name="static")
templates = Jinja2Templates(directory="./templates")

# 存储下载进度
download_progress = {}

# 添加 S3 配置
@lru_cache()
def get_s3_client():
    """缓存 S3 客户端实例"""
    return boto3.client(
        's3',
        aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
        region_name=os.environ.get('AWS_REGION', 'us-east-1')
    )

# 延迟初始化 S3 客户端
s3_client = None

@app.on_event("startup")
async def startup_event():
    """应用启动时初始化 S3 客户端"""
    global s3_client
    try:
        s3_client = get_s3_client()
        # 测试连接
        s3_client.list_buckets()
    except Exception as e:
        print(f"Failed to initialize S3 client: {e}")
        # 不要让应用崩溃，而是继续运行
        pass

BUCKET_NAME = os.environ.get('AWS_BUCKET_NAME')

def download_video(url: str, video_id: str):
    """后台下载视频的函数"""
    temp_dir = '/tmp'  # Vercel 允许使用 /tmp 目录
    output_template = f'{temp_dir}/%(title)s.%(ext)s'
    
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': output_template,
        'progress_hooks': [lambda d: update_progress(video_id, d)],
        'merge_output_format': 'mp4',
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            download_progress[video_id].update({
                'title': info.get('title', 'Unknown'),
                'status': 'starting'
            })
            ydl.download([url])
            
            # 上传到 S3
            filename = f"{info['title']}.mp4"
            file_path = f"{temp_dir}/{filename}"
            if os.path.exists(file_path):
                s3_client.upload_file(file_path, BUCKET_NAME, filename)
                os.remove(file_path)  # 清理临时文件
                
    except Exception as e:
        download_progress[video_id] = {
            'status': 'error',
            'error': str(e)
        }

def clean_text(text: str) -> str:
    """清理文本中的所有特殊字符和ANSI转义序列，并统一速度单位"""
    # 移除所有ANSI转义序列
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    text = ansi_escape.sub('', text)
    # 只保留基本可打印字符
    text = ''.join(char for char in text if ord(char) >= 32 and ord(char) <= 126)
    # 统一速度单位：将 MiB/s 转换为 MB/s
    text = text.replace('MiB/s', 'MB/s')
    text = text.replace('KiB/s', 'KB/s')
    text = text.replace('GiB/s', 'GB/s')
    return text.strip()

def update_progress(video_id: str, d: dict):
    """更新下载进度"""
    if d['status'] == 'downloading':
        try:
            # 使用clean_text函数清理所有进度信息
            percent = clean_text(d.get('_percent_str', '0%'))
            speed = clean_text(d.get('_speed_str', 'N/A'))
            eta = clean_text(d.get('_eta_str', 'N/A'))
            
            # 确保百分比格式正确
            if not percent.endswith('%'):
                percent = '0%'
            
            download_progress[video_id] = {
                'status': 'downloading',
                'percentage': percent,
                'speed': speed,
                'eta': eta
            }
        except Exception as e:
            download_progress[video_id] = {
                'status': 'downloading',
                'percentage': '0%',
                'speed': 'N/A',
                'eta': 'N/A'
            }
    elif d['status'] == 'finished':
        download_progress[video_id] = {
            'status': 'finished',
            'filename': d['filename']
        }

@app.get("/")
async def home(request: Request):
    """主页路由"""
    # 初始加载时不获取视频列表
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "videos": []}
    )

@app.get("/api/videos")
async def get_videos():
    """异步获取视频列表"""
    try:
        response = s3_client.list_objects_v2(Bucket=BUCKET_NAME)
        videos = []
        for obj in response.get('Contents', []):
            if obj['Key'].lower().endswith(('.mp4', '.flv', '.webm')):
                size_mb = obj['Size'] / (1024 * 1024)
                videos.append({
                    'name': os.path.splitext(obj['Key'])[0],
                    'filename': obj['Key'],
                    'size': f"{size_mb:.2f} MB",
                    'url': s3_client.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': BUCKET_NAME, 'Key': obj['Key']},
                        ExpiresIn=3600
                    )
                })
        return {"videos": videos}
    except Exception as e:
        return {"videos": [], "error": str(e)}

@app.post("/download")
async def download(background_tasks: BackgroundTasks, url: str = Form(...)):
    """下载视频的API端点"""
    video_id = str(hash(url))
    download_progress[video_id] = {'status': 'starting'}
    background_tasks.add_task(download_video, url, video_id)
    return {"video_id": video_id}

@app.get("/progress/{video_id}")
async def get_progress(video_id: str):
    """获取下载进度的API端点"""
    return JSONResponse(download_progress.get(video_id, {'status': 'not_found'}))

@app.delete("/video/{filename}")
async def delete_video(filename: str):
    """删除视频文件"""
    try:
        s3_client.delete_object(Bucket=BUCKET_NAME, Key=filename)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# 添加静态文件路由来访问下载的视频
app.mount("/videos", StaticFiles(directory="videos"), name="videos")

@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "ok"}

@app.get("/status")
async def status_check():
    """服务状态检查"""
    try:
        # 测试 S3 连接
        s3_client.list_buckets()
        s3_status = "connected"
    except Exception as e:
        s3_status = f"error: {str(e)}"

    return {
        "status": "ok",
        "s3_status": s3_status,
        "environment": "production" if os.environ.get('VERCEL') else "development"
    } 