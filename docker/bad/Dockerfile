# A release that was built and pushed but cannot start: the receiver binary sits at the path the chart
# runs as the API. The receiver exits at once because its own secret is not set in the API pod. Used by
# the bad-release experiment only.
# Build: docker build -f docker/bad/Dockerfile --build-arg BASE=dispatch-api:<tag> -t dispatch-api:bad .
# BASE has no useful default: without it the COPY below fails because /receiver does not exist in scratch.
ARG BASE=scratch
FROM ${BASE} AS good

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=good /receiver /dispatch
ENTRYPOINT ["/dispatch"]
