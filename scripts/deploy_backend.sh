#!/usr/bin/env bash
# Deploy both Cloud Run backends from ONE image and (re)create the Cloud Scheduler jobs (DEC-025).
#
# Usage:
#   scripts/deploy_backend.sh build [all|demo|personal]        # build a new image with Cloud Build, then deploy it
#   scripts/deploy_backend.sh <image-tag> [all|demo|personal]  # deploy an existing image tag
# The optional second argument deploys one service only (default: both). To try a new image on
# the demo first: `build demo`, check the demo, then `<image-tag> personal` (DEC-042).
#
# Services (same code, different settings and service accounts):
#   finance-backend       asia-northeast1 (Tokyo, next to the personal Supabase project)
#   finance-backend-demo  asia-northeast3 (Seoul, next to the demo Supabase project)
# Each service account can read only its own secrets (see scripts/phase1_infra.sh). The demo
# holds no credentials for your real database or Gmail.
# Secret values are never read or printed here: Cloud Run injects DB_PASS from Secret Manager.
set -euo pipefail

P=personal-finance-agent-507010
PN=328452160072
REPO=asia-northeast1-docker.pkg.dev/$P/pfa/finance-backend
PERSONAL_REGION=asia-northeast1
DEMO_REGION=asia-northeast3
SCHEDULER_REGION=asia-northeast1
# Model Armor templates (DEC-065) live in us-central1: on 2026-11-02 the Model Armor API refused
# asia-northeast1, asia-northeast3, asia-southeast1 and europe-west4 for this account, and only
# us-central1 answered. Both backends call it there (one template per environment).
MODEL_ARMOR_REGION=us-central1
PERSONAL_URL=https://finance-backend-$PN.$PERSONAL_REGION.run.app
DEMO_URL=https://finance-backend-demo-$PN.$DEMO_REGION.run.app
SCHED_SA=pfa-scheduler@$P.iam.gserviceaccount.com
CORS=https://personal-finance-agent-507010.web.app,https://personal-finance-agent-507010.firebaseapp.com
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ARG="${1:-}"
TARGET="${2:-all}"
if [[ -z "$ARG" ]]; then
  sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi
case "$TARGET" in
  all|demo|personal) ;;
  *) echo "Unknown target '$TARGET': use all, demo or personal." >&2; exit 1 ;;
esac
if [[ "$ARG" == "build" ]]; then
  TAG="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo manual)-$(date -u +%Y%m%d-%H%M%S)"
  echo "== Building $REPO:$TAG"
  (cd "$ROOT" && gcloud builds submit --tag "$REPO:$TAG" --project $P --timeout=1200s .)
else
  TAG="$ARG"
fi
IMAGE="$REPO:$TAG"

SMOKE=()  # "service region url" of each deployed service
# A rollback (`gcloud run services update-traffic --to-revisions=<old>=100`) pins traffic to that
# revision, and later deploys then serve 0 percent; each deploy below sends traffic to the new
# revision explicitly (DEC-042).
if [[ "$TARGET" != "demo" ]]; then
  echo "== Deploying personal backend ($PERSONAL_REGION)"
  gcloud run deploy finance-backend --project $P --region $PERSONAL_REGION --image "$IMAGE" \
    --service-account "pfa-backend-personal@$P.iam.gserviceaccount.com" \
    --allow-unauthenticated \
    --set-env-vars "^|^APP_ENV=personal|INTERNAL_AUDIENCE=$PERSONAL_URL|SCHEDULER_SA_EMAIL=$SCHED_SA|STATEMENT_BUCKET=$P-statements|CORS_ALLOWED_ORIGINS=$CORS|MODEL_ARMOR_TEMPLATE=projects/$P/locations/$MODEL_ARMOR_REGION/templates/pfa-guard-personal|SDP_ENABLED=1|ENABLE_CLOUD_TRACE=1" \
    --set-secrets "DB_PASS=pfa-db-password-personal:latest" \
    --memory 2Gi --cpu 1 --concurrency 20 --timeout 900 \
    --min-instances 0 --max-instances 3 --cpu-boost \
    --labels app=personal-finance-agent,env=personal \
    --quiet
  gcloud run services update-traffic finance-backend --project $P --region $PERSONAL_REGION --to-latest --quiet >/dev/null
  SMOKE+=("finance-backend $PERSONAL_REGION $PERSONAL_URL")
fi

if [[ "$TARGET" != "personal" ]]; then
  echo "== Deploying demo backend ($DEMO_REGION)"
  gcloud run deploy finance-backend-demo --project $P --region $DEMO_REGION --image "$IMAGE" \
    --service-account "pfa-backend-demo@$P.iam.gserviceaccount.com" \
    --allow-unauthenticated \
    --set-env-vars "^|^APP_ENV=demo|INTERNAL_AUDIENCE=$DEMO_URL|SCHEDULER_SA_EMAIL=$SCHED_SA|CORS_ALLOWED_ORIGINS=$CORS|MODEL_ARMOR_TEMPLATE=projects/$P/locations/$MODEL_ARMOR_REGION/templates/pfa-guard-demo|SDP_ENABLED=1|ENABLE_CLOUD_TRACE=1" \
    --set-secrets "DB_PASS=pfa-db-password-demo:latest" \
    --memory 1Gi --cpu 1 --concurrency 20 --timeout 600 \
    --min-instances 0 --max-instances 2 --cpu-boost \
    --labels app=personal-finance-agent,env=demo \
    --quiet
  gcloud run services update-traffic finance-backend-demo --project $P --region $DEMO_REGION --to-latest --quiet >/dev/null
  SMOKE+=("finance-backend-demo $DEMO_REGION $DEMO_URL")
fi

upsert_job() {  # name schedule uri audience deadline description
  local verb=create
  if gcloud scheduler jobs describe "$1" --location $SCHEDULER_REGION --project $P >/dev/null 2>&1; then
    verb=update
  fi
  gcloud scheduler jobs "$verb" http "$1" --location $SCHEDULER_REGION --project $P \
    --schedule "$2" --time-zone "Asia/Kolkata" --uri "$3" --http-method POST \
    --oidc-service-account-email "$SCHED_SA" --oidc-token-audience "$4" \
    --attempt-deadline "$5" --description "$6" --quiet >/dev/null
  echo "Scheduler job $1: $verb ($2 Asia/Kolkata)"
}
echo "== Cloud Scheduler jobs"
upsert_job pfa-gmail-sync-hourly "5 * * * *" "$PERSONAL_URL/internal/gmail-sync" "$PERSONAL_URL" 900s \
  "Hourly tick: syncs Gmail when the interval set on Bank Sync has passed (personal)"
upsert_job pfa-demo-reset-nightly "30 3 * * *" "$DEMO_URL/internal/demo-reset" "$DEMO_URL" 600s \
  "Nightly demo reset to sample data; also keeps the free demo database active"

echo "== Smoke checks"
for entry in "${SMOKE[@]}"; do
  read -r svc region url <<<"$entry"
  serving=$(gcloud run services describe "$svc" --project $P --region "$region" \
    --format='value(status.traffic[0].revisionName,status.traffic[0].percent)' | tr '\t' ' ')
  health=$(curl -s -o /dev/null -w "%{http_code}" --max-time 60 "$url/health")
  me=$(curl -s -o /dev/null -w "%{http_code}" --max-time 30 "$url/me")
  tx=$(curl -s -o /dev/null -w "%{http_code}" --max-time 30 "$url/transactions?limit=1")
  echo "$url  serving: $serving%  /health=$health (want 200)  /me=$me (want 401)  /transactions=$tx (want 401)"
done
echo "Deployed image $IMAGE ($TARGET)"
