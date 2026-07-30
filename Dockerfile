# dice-document-pipeline-api — dev image
#
# Scope (see ADR 0007 for the Java version call, and implementation_plan.md's
# Decisions for why Docker is staged this way): just enough to run the Django
# app plus veraPDF for the precheck/postcheck stages. Does NOT yet provision
# for the OpenDataLoader hybrid server or Docling model weights — that lands
# once OCRService/TaggingService actually exist, per the documented build
# order, so this image doesn't have to guess at their requirements.
#
# Run via docker-compose (app + Postgres — see docker-compose.yml):
#   docker compose up --build
#
# Run this image alone, no Postgres (falls back to sqlite per config/settings.py):
#   docker build -t dice-document-pipeline-api .
#   docker run -p 8000:8000 --env-file .env dice-document-pipeline-api

# ---- Stage 1: install veraPDF via its own unattended installer -------------
FROM eclipse-temurin:21-jdk-alpine AS verapdf-installer
ARG VERAPDF_VERSION=1.30
ARG VERAPDF_MINOR_VERSION=2
WORKDIR /tmp
COPY verapdf-install.xml .
RUN wget -O verapdf-installer.zip \
        "https://software.verapdf.org/releases/${VERAPDF_VERSION}/verapdf-greenfield-${VERAPDF_VERSION}.${VERAPDF_MINOR_VERSION}-installer.zip" \
    && unzip verapdf-installer.zip \
    && java -jar "verapdf-greenfield-${VERAPDF_VERSION}.${VERAPDF_MINOR_VERSION}/verapdf-izpack-installer-${VERAPDF_VERSION}.${VERAPDF_MINOR_VERSION}.jar" verapdf-install.xml

# ---- Stage 2: a minimal Java 21 runtime, just the modules veraPDF needs ----
# Same module list veraPDF's own Dockerfile uses (veraPDF/veraPDF-apps).
FROM eclipse-temurin:21-jdk-alpine AS jre-builder
RUN "$JAVA_HOME/bin/jlink" \
        --add-modules java.base,java.compiler,java.logging,java.xml,jdk.crypto.ec,java.desktop,jdk.management \
        --strip-debug --no-man-pages --no-header-files --compress=2 \
        --output /javaruntime

# ---- Final: Python 3.12 + the Java 21 JRE + veraPDF ------------------------
# Alpine throughout on purpose: the JRE above is built against musl libc
# (Alpine), and copying musl-linked binaries into a glibc image like
# python:3.12-slim can break at runtime. Staying on Alpine for the final
# stage avoids that mismatch.
FROM python:3.12-alpine

ENV JAVA_HOME=/opt/java/openjdk \
    PATH="/opt/java/openjdk/bin:/opt/verapdf:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY --from=jre-builder /javaruntime $JAVA_HOME
COPY --from=verapdf-installer /opt/verapdf /opt/verapdf

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY manage.py .
COPY config/ ./config/
COPY accounts/ ./accounts/
COPY api/ ./api/
COPY remediation/ ./remediation/

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
