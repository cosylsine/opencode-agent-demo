# 此基础镜像已经包含所有依赖；随 images/offline-images.tar.gz 一同提供。
# 可在彻底断网的环境重建：bash deploy.sh rebuild
FROM opencode-agent-demo-runtime:20261018-office-amd64
WORKDIR /app
COPY source/ /app/
COPY build/ /opt/deploy/
COPY runtime/ripgrep/rg /opt/bin/rg
RUN ln -s /opt/venv /app/.venv \
 && mkdir -p /app/bin /opt/catalog-seed \
 && ln -s /opt/bin/opencode /app/bin/opencode \
 && cp -a /app/.opencode/. /opt/catalog-seed/ \
 && ln -s /opt/dashboard/node_modules /app/apps/dashboard/node_modules \
 && python scripts/export_contracts.py \
 && npm run generate --prefix apps/dashboard \
 && npm run build --prefix apps/dashboard \
 && python -c "import fastapi, numpy, mcp, sqlalchemy; print('Python dependencies OK')" \
 && test "$(/opt/bin/opencode --version)" = '1.18.31' \
 && rg --version \
 && rg --no-config --files --hidden --glob='SKILL.md' .opencode/skills | sort
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    OPENCODE_DISABLE_AUTOUPDATE=true OPENCODE_DISABLE_MODELS_FETCH=true \
    OPENCODE_DISABLE_DEFAULT_PLUGINS=true OPENCODE_DISABLE_LSP_DOWNLOAD=true \
    OPENCODE_DISABLE_SHARE=true OPENCODE_DISABLE_EXTERNAL_SKILLS=true \
    OPENCODE_DISABLE_CLAUDE_CODE=true OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER=true \
    NO_PROXY=* no_proxy=* HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= \
    http_proxy= https_proxy= all_proxy=
HEALTHCHECK --interval=20s --timeout=8s --start-period=60s --retries=3 \
    CMD ["python", "/opt/deploy/runtime.py", "health"]
ENTRYPOINT ["python", "/opt/deploy/runtime.py"]
CMD ["serve"]
