FROM python:3.13-slim
# numpy is required: without it world.py cannot load and the bot plays blind
RUN pip install --no-cache-dir numpy==2.2.6
WORKDIR /bot
COPY . .
# fail the build (not the match) if the world/bot cannot import; precompile; readable by uid 65534
RUN python3 -c "import numpy, precompute, world, bot" && python3 -m compileall -q /bot && chmod -R a+rX /bot
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
