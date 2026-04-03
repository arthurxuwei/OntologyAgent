# syntax=docker/dockerfile:1.7
FROM freqtradeorg/freqtrade:stable

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENTRYPOINT ["python", "run_stack.py"]
