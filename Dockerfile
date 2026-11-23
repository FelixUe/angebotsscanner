# Angebotsscanner – ein schlanker Container mit eingebautem Zeitplan.
#
# Die Konfiguration im Image ist config.example.yaml (Märkte, Regeln,
# Zeitplan). Was privat ist, kommt aus der Umgebung: PLZ, Mealie-URL und
# -Token. Preishistorie, Cache und Zustand liegen im Volume /daten.

FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m venv /opt/venv && /opt/venv/bin/pip install .

FROM python:3.12-slim
LABEL org.opencontainers.image.title="Angebotsscanner" \
      org.opencontainers.image.description="Supermarkt-Angebote gegen Essensplan und Vorrat, Ergebnis in Mealie" \
      org.opencontainers.image.source="https://github.com/FelixUe/angebotsscanner"
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    TZ=Europe/Berlin \
    ANGEBOTSSCANNER_CONFIG=/app/config.yaml \
    ANGEBOTSSCANNER_VORRAT=/daten/vorrat.yaml \
    ANGEBOTSSCANNER_DATEN=/daten
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY config.example.yaml ./config.yaml
RUN useradd --create-home --uid 10001 scanner \
    && mkdir -p /daten && chown scanner:scanner /daten
VOLUME ["/daten"]
USER scanner
HEALTHCHECK --interval=2m --timeout=10s --start-period=30s --retries=2 \
    CMD ["angebotsscanner", "gesund"]
ENTRYPOINT ["angebotsscanner"]
CMD ["dienst"]
