# 7. Java version: 21, not 25

## Status

Accepted

## Context

`VeraPDFAdapter` (`precheck`/`postcheck`) and OpenDataLoader (`ocr`/`finalize_tags`, see [ADR 0004](0004-ocr-tagging-engine.md)) both need a local JVM. veraPDF explicitly supports Java 8/11/17/21/25; OpenDataLoader requires 11+ with no documented upper bound. Java 25 is the newest LTS, but it has a live, unresolved JDK regression breaking `ServiceLoader` under JPMS ([JDK-8371520](https://bugs.openjdk.org/browse/JDK-8371520)) — a mechanism Apache PDFBox, which underlies both tools' Java engines, is known to rely on for plugin-style discovery (font/image providers). No report of this hitting either tool by name yet, but the risk is concrete, not hypothetical.

## Decision

Use Java 21 for local dev and the Docker image, not Java 25. Both are LTS releases (21 supported through roughly 2031); 21 has no known JPMS/`ServiceLoader` issues, and both tools explicitly support it.

## Consequences

- Java 21 gets pinned in the Dockerfile and local dev setup, not the newest available LTS.
- Revisit if `JDK-8371520` gets fixed and there's an actual reason to move to 25, or if either tool later requires something newer than 21.
