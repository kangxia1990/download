from fastapi import FastAPI, Request, BackgroundTasks, Form
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, RedirectResponse
import yt_dlp
import os
import asyncio
from pathlib import Path
import re
from functools import lru_cache
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# 获取项目根目录
BASE_DIR = Path(__file__).resolve().parent
TEMP_DIR = Path('/tmp' if os.environ.get('VERCEL') else './videos')
TEMP_DIR.mkdir(exist_ok=True)

# 确保目录存在
for directory in ['static', 'templates']:
    Path(directory).mkdir(exist_ok=True)

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 配置静态文件和模板
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/tmp", StaticFiles(directory="/tmp"), name="tmp")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# 存储下载进度
download_progress = {}

def download_video(url: str, video_id: str):
    """后台下载视频的函数"""
    output_template = str(TEMP_DIR / '%(title)s.%(ext)s')
    
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
    videos = []
    try:
        for file in TEMP_DIR.glob("*.mp4"):
            if file.is_file():
                videos.append({
                    'name': file.stem,
                    'filename': file.name,
                    'size': f"{file.stat().st_size / (1024*1024):.2f} MB",
                    'url': f"/tmp/{file.name}"
                })
    except Exception as e:
        print(f"Error listing videos: {e}")
    
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "videos": videos}
    )

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
        file_path = TEMP_DIR / filename
        if file_path.exists():
            os.remove(file_path)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "ok"}

@app.get("/status")
async def status_check():
    """服务状态检查"""
    return {
        "status": "ok",
        "environment": "production" if os.environ.get('VERCEL') else "development"
    } 