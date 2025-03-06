from main import app
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 导出 FastAPI 应用
def handler(request, context):
    try:
        return app(request, context)
    except Exception as e:
        logger.error(f"Error handling request: {e}")
        raise 