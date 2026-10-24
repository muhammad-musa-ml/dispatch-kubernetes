# Migration runner: psql plus the Dispatch migrations from the pinned submodule.
# Build from the repository root: docker build -f docker/migrate/Dockerfile -t dispatch-migrate .
FROM postgres:17-alpine
COPY docker/migrate/migrate.sh /usr/local/bin/migrate.sh
COPY vendor/dispatch-api/migrations /migrations
RUN chmod 0755 /usr/local/bin/migrate.sh
USER postgres
ENTRYPOINT ["/usr/local/bin/migrate.sh"]
