# Use a slim Python image with Java support
FROM python:3.9-slim-bullseye

# Set the working directory in the container
WORKDIR /app

# Install system dependencies including curl, Chrome, Java, and BrowserMob Proxy
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    default-jre \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list' \
    && apt-get update \
    && apt-get install -y google-chrome-stable=129.0.6668.89-1 \
    && apt-mark hold google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Install ChromeDriver
RUN wget -N https://storage.googleapis.com/chrome-for-testing-public/129.0.6668.89/linux64/chromedriver-linux64.zip -P ~/ && \
    unzip ~/chromedriver-linux64.zip -d ~/ && \
    rm ~/chromedriver-linux64.zip && \
    mv -f ~/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver && \
    rm -rf ~/chromedriver-linux64 && \
    chown root:root /usr/local/bin/chromedriver && \
    chmod 0755 /usr/local/bin/chromedriver

# Install BrowserMob Proxy
RUN wget https://github.com/lightbody/browsermob-proxy/releases/download/browsermob-proxy-2.1.4/browsermob-proxy-2.1.4-bin.zip && \
    unzip browsermob-proxy-2.1.4-bin.zip && \
    mv browsermob-proxy-2.1.4 /opt/browsermob-proxy && \
    rm browsermob-proxy-2.1.4-bin.zip

# Set BROWSERMOB_PROXY_PATH environment variable
ENV BROWSERMOB_PROXY_PATH=/opt/browsermob-proxy/bin/browsermob-proxy

# Ensure proper file permissions on the proxy executable
RUN chmod +x /opt/browsermob-proxy/bin/browsermob-proxy

# Add BrowserMob Proxy to PATH
ENV PATH="/opt/browsermob-proxy/bin:${PATH}"

# Upgrade pip
RUN pip install --no-cache-dir --upgrade pip

# Copy the requirements file into the container
COPY requirements.txt .

# Install the required packages
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install chromium

# Copy the application code into the container
COPY . .

# Create logs directory and set permissions
RUN mkdir -p /app/logs && chmod 777 /app/logs

# Expose the port the app runs on
EXPOSE 8000

# Set environment variable to disable output buffering
ENV PYTHONUNBUFFERED=1

# Change the CMD to use unbuffered output and include setup verification
CMD ["python", "-u", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]