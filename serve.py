from waitress import serve
from app import create_app
import logging

# Configure basic logging for Waitress
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('waitress')

app = create_app()

if __name__ == '__main__':
    logger.info("==============================================================")
    logger.info("  VIGNESH GROWTH LAB POS - PRODUCTION WSGI SERVER (WAITRESS)")
    logger.info("  Running on http://0.0.0.0:5000")
    logger.info("  Configured: 16 Worker Threads, 500 Connection Limit")
    logger.info("==============================================================")
    
    serve(
        app,
        host='0.0.0.0',
        port=5000,
        threads=16,
        connection_limit=500,
        channel_timeout=30
    )
