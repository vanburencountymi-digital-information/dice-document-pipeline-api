# dice-document-pipeline-api — dev image
#
# Scope (see ADR 0007 for the Java version call, and implementation_plan.md's
# Decisions for why Docker is staged this way): the Django app, veraPDF for the
# precheck/postcheck stages, and Java + the `opendataloader-pdf` pip package
# (in requirements.txt) for the ocr stage — patched to our own fork (ADR 0010)
# to fix a confirmed upstream bug in hybrid mode's tagged-pdf output.
#
# Deliberately NOT here: the OpenDataLoader hybrid AI backend
# (`opendataloader-pdf-hybrid`, ADR 0004's "--hybrid docling-fast") or Docling's
# model weights — that server needs PyTorch/Docling, which don't ship
# musl/Alpine wheels, so it lives in its own glibc-based image
# (Dockerfile.opendataloader-hybrid) and its own docker-compose service
# instead of fighting this image's Alpine base. See ADR 0004's Consequences.
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

# ---- Stage 3: build our patched opendataloader-pdf jar (ADR 0010) ----------
# TODO(ADR 0010): temporary — remove this stage and go back to a plain
# `pip install opendataloader-pdf==<version>` once our fix is merged upstream
# and released. See docs/adrs/0010-fix-opendataloader-hybrid-tagging-upstream.md.
FROM eclipse-temurin:21-jdk-alpine AS opendataloader-pdf-builder
WORKDIR /build
RUN apk add --no-cache git maven
RUN git clone --branch fix/hybrid-tagged-pdf-text-drop \
        https://github.com/ViolanteCodes/opendataloader-pdf.git . \
    && git checkout a55c694
# Pinned to a55c694 (fixes OCR-fallback text landing at the content-stream tail
# instead of its correct reading-order position — see that commit's message) rather
# than floating on the branch HEAD, so a future push to this branch can't silently
# change what this build pulls in.
# The Maven aggregator POM lives at java/pom.xml, not the repo root — module names in
# -pl are relative to it (plain opendataloader-pdf-core/-cli, no java/ prefix).
WORKDIR /build/java
RUN mvn -pl opendataloader-pdf-core,opendataloader-pdf-cli -am package -DskipTests

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

# Overwrite opendataloader_pdf's bundled CLI jar with our patched build (ADR 0010) — the
# package always installs to this exact site-packages path regardless of pip's resolution,
# and our from-source build always produces this filename (the repo's pom.xml version is an
# unset "0.0.0" placeholder; real version numbers are only assigned by upstream's own release
# pipeline, which we're not using). Remove this override once our fix is merged upstream and
# released, and go back to a plain `pip install opendataloader-pdf==<version>`.
COPY --from=opendataloader-pdf-builder \
     /build/java/opendataloader-pdf-cli/target/opendataloader-pdf-cli-0.0.0.jar \
     /usr/local/lib/python3.12/site-packages/opendataloader_pdf/jar/opendataloader-pdf-cli.jar

COPY manage.py .
COPY config/ ./config/
COPY accounts/ ./accounts/
COPY api/ ./api/
COPY remediation/ ./remediation/

# Make app created files locally editable
RUN addgroup -g 1000 app && adduser -D -u 1000 -G app app
USER app

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
