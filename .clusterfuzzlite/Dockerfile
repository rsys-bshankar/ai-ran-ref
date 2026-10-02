# ClusterFuzzLite build image. The OSS-Fuzz builder tracks `latest` by design
# (it carries the sanitizer toolchain); do not pin it by digest.
FROM gcr.io/oss-fuzz-base/base-builder-python
COPY . $SRC/ai-ran-ref
COPY .clusterfuzzlite/build.sh $SRC/
WORKDIR $SRC/ai-ran-ref/smo
