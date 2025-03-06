from fastapi import FastAPI, Request, BackgroundTasks, Form
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, RedirectResponse
import yt_dlp
import os
import asyncio
from pathlib import Path
import re

# 确保必要的目录存在
for directory in ['static', 'videos', 'templates']:
    Path(directory).mkdir(exist_ok=True)

app = FastAPI()

# 配置静态文件和模板
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# 创建视频存储目录
VIDEOS_DIR = Path("videos")
VIDEOS_DIR.mkdir(exist_ok=True)

# 存储下载进度
download_progress = {}

def download_video(url: str, video_id: str):
    """后台下载视频的函数"""
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',  # 选择最佳视频和音频质量
        'outtmpl': f'videos/%(title)s.%(ext)s',
        'progress_hooks': [lambda d: update_progress(video_id, d)],
        # B站特定配置
        'cookiesfrombrowser': ['chrome'],  # 从Chrome浏览器获取cookies
        'extractor_args': {
            'bilibili': {
                'cookie': [],  # 如果需要可以添加cookie
            }
        },
        # 下载设置
        'merge_output_format': 'mp4',  # 将视频合并为mp4格式
        'writethumbnail': True,  # 下载缩略图
        'writesubtitles': True,  # 下载字幕（如果有）
        'subtitleslangs': ['zh-CN'],  # 下载中文字幕
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # 先获取视频信息
            info = ydl.extract_info(url, download=False)
            # 更新下载状态
            download_progress[video_id].update({
                'title': info.get('title', 'Unknown'),
                'duration': info.get('duration', 0),
                'status': 'starting'
            })
            # 开始下载
            ydl.download([url])
    except Exception as e:
        download_progress[video_id] = {
            'status': 'error', 
            'error': str(e),
            'details': '请确保链接正确且视频可以正常访问'
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
    for file in VIDEOS_DIR.glob("*"):
        if file.is_file() and file.suffix.lower() in ['.mp4', '.flv', '.webm']:
            videos.append({
                'name': file.stem,
                'filename': file.name,
                'size': f"{file.stat().st_size / (1024*1024):.2f} MB",
                'url': f"/videos/{file.name}"  # 视频访问URL
            })
    
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
        file_path = VIDEOS_DIR / filename
        if file_path.exists():
            os.remove(file_path)
            # 同时删除可能存在的缩略图和字幕文件
            for ext in ['.jpg', '.png', '.vtt', '.srt']:
                thumb_path = file_path.with_suffix(ext)
                if thumb_path.exists():
                    os.remove(thumb_path)
            return {"status": "success"}
        return {"status": "file_not_found"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# 添加静态文件路由来访问下载的视频
app.mount("/videos", StaticFiles(directory="videos"), name="videos") 