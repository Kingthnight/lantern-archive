# Hosted demo: GitHub, Render, and DigitalOcean

The offline application runs with `python app.py --model ...`. The hosted demo
uses `hosted:application`, a separate WSGI entry point. Render runs the small
Python web service; DigitalOcean Serverless Inference runs the open model.
No model weights, GPU, persistent disk, or database are needed on Render.

## 1. GitHub

Create a public `lantern-archive` repository and push this folder, including
`render.yaml`, `hosted.py`, `requirements.txt`, and the static files. The existing
offline instructions and MIT license stay in the repository. `.gitignore`
excludes local models, secrets in `.env` files, logs, and virtual environments.

## 2. DigitalOcean

1. Open **Serverless Inference** and **Model Access Keys**.
2. Create a dedicated key named `lantern-archive-demo`. Scope it to the chosen
   model if the key form offers that option. Avoid a broad account API token.
3. The initial hosted model is **Llama 4 Maverick**, API ID `llama-4-maverick`,
   which DigitalOcean currently lists as an available open-weight model.
4. Check that your signup credits are usable for Serverless Inference. Complete
   billing onboarding yourself if DigitalOcean requires it before issuing a key
   or accepting requests. Do not enable automatic balance reload for this demo.
5. Keep the key for Render's secret environment field. Never put it in GitHub,
   the frontend, screenshots, or the submission article.

Official references:
[model access keys](https://docs.digitalocean.com/products/inference/how-to/manage-model-access-keys/),
[models](https://docs.digitalocean.com/products/inference/details/models/),
[serverless prepayment](https://docs.digitalocean.com/products/inference/how-to/manage-serverless-inference-prepayment/).

## 3. Render

Create a **Web Service** from the GitHub repository, or use a **Blueprint** to
import `render.yaml`. For a manually configured service use:

| Setting | Value |
| --- | --- |
| Runtime | Python 3 |
| Branch | The branch you pushed |
| Root directory | Leave empty |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn hosted:application --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120` |
| Instance type | Free |
| Health check path | `/healthz` |
| Secret `DO_MODEL_ACCESS_KEY` | The DigitalOcean Model Access Key |
| `DO_MODEL` | `llama-4-maverick` |
| `DEMO_REQUESTS_PER_HOUR` | `60` |

Render provides `PORT` and `RENDER_EXTERNAL_URL`. For a custom domain, set
`PUBLIC_ORIGIN` to that domain's HTTPS origin. Do not add a path or trailing query.
The app uses the configured origin to reject cross-origin browser submissions.

## 4. Verify the real deployment

1. Open `/healthz`: expect `{"status": "ok"}`. This check never calls the model.
2. Open the app and confirm that **PUBLIC DEMO** and the hosted privacy notice
   appear. If the API key is missing, the page reports incomplete setup.
3. Click **Try a fictional example**, then **Ask a question** and
   **Write a remembrance**. Check all three facts and source numbers.
4. Confirm that copying and downloading work. Record this real end-to-end run
   before claiming that the hosted model has been tested.

The server accepts at most 10 KB per request, permits one inference at a time,
and limits the whole demo to 60 inference attempts in a rolling hour. These
limits are in memory and reset on a restart or free-tier sleep. Keep one
Gunicorn worker and one Render instance; this is a small demo guardrail, not a
durable billing cap. There are no automatic inference retries. Upstream rate
limits trigger a one-minute cooldown.

Unlike the local model's schema-constrained output, the hosted provider is
prompted to return JSON. Shared application code validates the JSON shape and
the line count before showing a draft. Neither mode proves factual accuracy.

Submitted names and notes pass through Render to DigitalOcean. The app does
not write them to disk or application logs. The page discloses this and
encourages fictional examples. Browser notes remain in `localStorage`.

## Free-tier sleep and ending the demo

Render Free services sleep after 15 minutes without inbound traffic and may
take about a minute to wake. The hosted page checks status once rather than
polling continuously. No uptime-ping workaround is configured.

For your planned seven-day run, manually suspend the service from its Render
dashboard when finished, and revoke the demo's DigitalOcean key if it is no
longer needed. No automatic shutdown is configured. Leave the repository and
video available so the submission can still be reviewed after the live demo ends.

[Render Free documentation](https://render.com/docs/free)

## Local smoke checks

The WSGI application can be exercised on Windows with Python's `wsgiref`
server and a mock inference client. Gunicorn itself runs on the Linux Render
service. Without a real DigitalOcean key, smoke checks do not verify model
availability, credit eligibility, output quality, or provider authentication.
