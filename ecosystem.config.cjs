module.exports = {
  apps: [{
    name: 'ai-forex-agent',
    script: 'venv/bin/python',
    args: 'main.py --port 8000',
    cwd: '/opt/ai-forex-agent',
    interpreter: 'none',
    instances: 1,
    autorestart: true,
    watch: false,
    max_memory_restart: '500M',
    env: {
      TRADING_MODE: 'real',
      NODE_ENV: 'production',
      TZ: 'Asia/Jakarta'
    }
  }]
}
