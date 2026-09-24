# SearxNG for cycle 1's WEB_SEARCH_BASE_URL (Railway service, root dir /searxng).
# Same settings as the local compose (JSON output on, limiter off). The
# placeholder secret_key is replaced with a random value at build time, so
# no secret is committed or typed by hand. Keep this service private: no
# public domain; evorove_lead reaches it over the project's private network.
FROM alpine:3.20 AS settings
COPY settings.yml /settings.yml
RUN secret="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" \
    && sed -i "s|local_development_only_change_before_any_real_deployment|${secret}|" /settings.yml \
    && ! grep -q local_development_only /settings.yml

FROM searxng/searxng:latest
COPY --from=settings /settings.yml /etc/searxng/settings.yml
