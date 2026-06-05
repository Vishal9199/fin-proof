# 3.13 = the interpreter the full test suite + eval gates are verified on
# (pandas 3.x / pandera / langgraph 1.x all green). 3.14-slim is also published
# if you want to move up — rebuild and run `pytest` + `evals.run` to re-verify.
FROM python:3.13-slim

WORKDIR /app

# Install deps first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
