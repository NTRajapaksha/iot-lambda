FROM python:3.9-slim

WORKDIR /app

# Add Debian Bullseye repo and install Java
RUN echo "deb http://deb.debian.org/debian bullseye main" > /etc/apt/sources.list.d/bullseye.list \
    && apt-get update && apt-get install -y \
    openjdk-11-jre-headless \
    procps \
    && rm -rf /var/lib/apt/lists/* \
    && rm /etc/apt/sources.list.d/bullseye.list

ENV JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]