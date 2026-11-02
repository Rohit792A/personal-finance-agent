#!/usr/bin/env bash
# Phase 1 infrastructure (DEC-025): idempotent. Creates the image registry, per-environment
# service accounts with least-privilege grants, the statement-passwords secret shell and the
# private statements bucket. Never reads or prints secret values.
set -uo pipefail
P=personal-finance-agent-507010
PERSONAL_SA=pfa-backend-personal@$P.iam.gserviceaccount.com
DEMO_SA=pfa-backend-demo@$P.iam.gserviceaccount.com
SCHED_SA=pfa-scheduler@$P.iam.gserviceaccount.com
BUCKET=gs://$P-statements

step() { echo; echo "== $*"; }

step "Enable Cloud Scheduler API"
gcloud services enable cloudscheduler.googleapis.com --project $P

step "Artifact Registry repo 'pfa' (asia-northeast1)"
gcloud artifacts repositories describe pfa --location=asia-northeast1 --project $P >/dev/null 2>&1 \
  || gcloud artifacts repositories create pfa --repository-format=docker --location=asia-northeast1 \
       --description="Personal Finance AI backend images" --project $P

step "Service accounts"
for sa in "pfa-backend-personal:PFA backend (personal)" "pfa-backend-demo:PFA backend (demo)" "pfa-scheduler:PFA Cloud Scheduler caller"; do
  id="${sa%%:*}"; name="${sa#*:}"
  gcloud iam service-accounts describe "$id@$P.iam.gserviceaccount.com" --project $P >/dev/null 2>&1 \
    || gcloud iam service-accounts create "$id" --display-name="$name" --project $P
done
sleep 10  # IAM propagation for new service accounts

step "Vertex AI access (Gemini) for both backends"
for sa in $PERSONAL_SA $DEMO_SA; do
  gcloud projects add-iam-policy-binding $P --member="serviceAccount:$sa" --role=roles/aiplatform.user \
    --condition=None --quiet >/dev/null && echo "aiplatform.user -> $sa"
done

step "Secret shell for statement PDF passwords (no value yet)"
gcloud secrets describe pfa-statement-passwords --project $P >/dev/null 2>&1 \
  || gcloud secrets create pfa-statement-passwords --replication-policy=automatic \
       --labels=app=personal-finance-agent --project $P

step "Per-secret grants (each backend reads only its own secrets)"
for s in pfa-db-password-personal pfa-gmail-app-password pfa-statement-passwords; do
  gcloud secrets add-iam-policy-binding $s --member="serviceAccount:$PERSONAL_SA" \
    --role=roles/secretmanager.secretAccessor --project $P --quiet >/dev/null && echo "accessor  $s -> personal"
done
for s in pfa-gmail-app-password pfa-statement-passwords; do
  gcloud secrets add-iam-policy-binding $s --member="serviceAccount:$PERSONAL_SA" \
    --role=roles/secretmanager.secretVersionManager --project $P --quiet >/dev/null && echo "versionMgr $s -> personal"
done
gcloud secrets add-iam-policy-binding pfa-db-password-demo --member="serviceAccount:$DEMO_SA" \
  --role=roles/secretmanager.secretAccessor --project $P --quiet >/dev/null && echo "accessor  pfa-db-password-demo -> demo"

step "Private statements bucket"
gcloud storage buckets describe $BUCKET --project $P >/dev/null 2>&1 \
  || gcloud storage buckets create $BUCKET --project $P --location=asia-northeast1 \
       --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets add-iam-policy-binding $BUCKET --member="serviceAccount:$PERSONAL_SA" \
  --role=roles/storage.objectAdmin --project $P --quiet >/dev/null && echo "objectAdmin $BUCKET -> personal"

step "Summary"
gcloud iam service-accounts list --project $P --format="table(email,displayName)" | grep -E "pfa-|EMAIL"
gcloud storage buckets describe $BUCKET --format="value(name,location,iamConfiguration.publicAccessPrevention,iamConfiguration.uniformBucketLevelAccess.enabled)"
