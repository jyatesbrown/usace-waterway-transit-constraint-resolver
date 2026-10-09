FROM apify/actor-python:3.12

COPY --chown=myuser:myuser requirements.txt ./

RUN echo "Python version:" \
 && python --version \
 && pip install --no-cache-dir -r requirements.txt \
 && echo "Installed packages:" \
 && pip freeze

COPY --chown=myuser:myuser . ./

RUN python -m compileall -q src/

CMD ["python", "-m", "src"]
