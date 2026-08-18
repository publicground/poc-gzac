# Contributing

Thank you for contributing to poc-gzac. This guide covers how the repository is structured and how to make changes safely.

---

## Table of Contents

- [Repository conventions](#repository-conventions)
- [Adding or modifying an application](#adding-or-modifying-an-application)
- [Adding a new environment](#adding-a-new-environment)
- [Working with secrets](#working-with-secrets)
- [Infrastructure changes](#infrastructure-changes)
- [Git workflow](#git-workflow)
- [Validating changes](#validating-changes)
- [ArgoCD operations](#argocd-operations)

---

## Repository conventions

### Values file layering

Each app or infrastructure component uses three levels of Helm values, merged in order:

```
values.yaml                 ← shared defaults (all clusters)
values.<env>.yaml           ← environment-specific overrides
additionalmanifests/<env>/  ← extra K8s manifests (HTTPRoute, SealedSecret, CNPG cluster, etc.)
```

Only put values in `values.yaml` that are truly environment-agnostic. Put everything cluster-specific (URLs, image tags for specific registries, feature flags, resource limits) in the env-specific file.

### `.argocd.yaml` metadata

Each app/infrastructure directory contains an `.argocd.yaml` (consumed by the ApplicationSets in `appsets/`). This file controls:

- Which Helm chart and repository to use
- Which environments the app is enabled in (`clusters.<env>.enabled: 'true'`)
- Chart version per environment
- Paths to additional manifests

Do not rename or move this file without updating the corresponding ApplicationSet generator.

### Namespace

All applications deploy into the `publicground` namespace. Infrastructure operators use their own namespaces (e.g., `cnpg-system`, `redis-system`, `sealed-secrets`).

---

## Adding or modifying an application

### Modifying an existing app

1. Edit `apps/<app>/values.<env>.yaml` for the target environment.
2. If you need extra Kubernetes manifests (e.g., a new HTTPRoute or SealedSecret), add them to `apps/<app>/additionalmanifests/<env>/`.
3. Open a PR — Argo CD will pick up changes on the next sync after merge.

### Adding a new application

1. Create the directory structure:

   ```
   apps/<new-app>/
   ├── .argocd.yaml
   ├── values.yaml
   ├── values.local.yaml
   ├── values.655-dmn-poc.yaml
   └── additionalmanifests/
       ├── local/
       └── 655-dmn-poc/
   ```

2. Fill in `.argocd.yaml` following the pattern of an existing app (e.g., `apps/openzaak/.argocd.yaml`).

3. Set `clusters.<env>.enabled: 'false'` for any environment where the app should not deploy yet.

4. Add an `HTTPRoute` manifest to `additionalmanifests/<env>/` for each environment where the app needs ingress.

5. If the app needs a PostgreSQL database, add a CNPG `Cluster` manifest to `additionalmanifests/<env>/cnpg.yaml`.

6. If the app needs a Redis instance, add a Redis CR to `additionalmanifests/<env>/redis.yaml`.

7. If the app has secrets, create a `SealedSecret` (see [Working with secrets](#working-with-secrets)) and commit it to `additionalmanifests/<env>/`.

---

## Adding a new environment

1. Create a new overlay directory for each app and infrastructure component that needs environment-specific config:

   ```
   apps/<app>/values.<new-env>.yaml
   apps/<app>/additionalmanifests/<new-env>/
   infrastructure/<component>/values.<new-env>.yaml
   ```

2. Create the ApplicationSets for the new environment:

   ```
   appsets/<new-env>/publicground-apps.yaml
   appsets/<new-env>/publicground-services.yaml
   ```

   Copy the structure from `appsets/655-dmn-poc/` and update:
   - `repoURL` (git repo for the new cluster)
   - `targetRevision` (branch)
   - Cluster selector labels
   - Any `ignoreDifferences` rules specific to the cluster

3. Enable apps in `.argocd.yaml` by adding `clusters.<new-env>.enabled: 'true'`.

---

## Working with secrets

All secrets are stored as **SealedSecrets** — encrypted with the target cluster's public key. The raw secret value is **never** committed to git.

### Sealing a secret for 655-dmn-poc

```bash
# Ensure you are targeting the correct cluster
kubectl config use-context aks-655-dmn-poc

# Seal a single key from stdin
echo -n "my-secret-value" | kubeseal --raw \
  --from-file=/dev/stdin \
  --namespace publicground \
  --name <secret-name> \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets
```

Paste the resulting base64-encoded cipher into the appropriate key in `apps/<app>/additionalmanifests/655-dmn-poc/<sealed-secret-file>.yaml`.

### SealedSecret file format

```yaml
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: <secret-name>
  namespace: publicground
spec:
  encryptedData:
    MY_KEY: <sealed-value>
  template:
    metadata:
      name: <secret-name>
      namespace: publicground
```

### Sealing for local

Use the **Sealed Secrets Web UI** at `http://sealed-secrets-web.wigo4it.nl` when the local cluster is running.

### Rotating a secret

1. Generate the new sealed value using `kubeseal --raw` against the target cluster.
2. Replace the value in the SealedSecret manifest.
3. Commit and push — Argo CD will sync and the controller will update the underlying Secret.

### Important: cluster-scoped seals

Each sealed value is bound to a specific cluster's private key. A value sealed for `aks-655-dmn-poc` **cannot** be decrypted by the local cluster's controller, and vice versa.

---

## Infrastructure changes

Infrastructure components (operators, gateway, storage) follow the same layering pattern as applications. Their manifests live in `infrastructure/`.

- **Helm-based**: edit `infrastructure/<component>/values.<env>.yaml`
- **Kustomize-based**: edit `infrastructure/<component>/overlays/<env>/kustomization.yaml` and patches

Operators (CNPG, Redis, Keycloak) deploy before applications due to Argo CD sync wave ordering (`sync-wave: "1"` for services, `"2"` for apps).

---

## Git workflow

1. Create a feature branch from the current working branch:

   ```bash
   git checkout -b feature/<description>
   ```

2. Make your changes and validate them (see [Validating changes](#validating-changes)).

3. Commit with a descriptive message:

   ```
   feat(openzaak): add configuration for open-formulieren consumer
   fix(gzac-backend): correct CORS origin pattern
   chore(sealed-secrets): rotate gzac-backend plugin encryption key
   ```

4. Push and open a pull request against the main working branch.

5. After merge, Argo CD will automatically sync the affected apps within its polling interval (default: 3 minutes), or you can trigger a manual sync.

---

## Validating changes

```bash
# Lint YAML and validate against Kubernetes schemas
task validate
```

This runs `yamllint` and `kubeconform` over all manifests.

For Helm templates you can also render and inspect locally:

```bash
helm template <release-name> <chart> \
  -f apps/<app>/values.yaml \
  -f apps/<app>/values.655-dmn-poc.yaml
```

---

## ArgoCD operations

The 655-dmn-poc cluster uses Argo CD in **core mode** (no separate server process). All CLI operations require:

```bash
export ARGOCD_OPTS="--core"
kubectl config use-context aks-655-dmn-poc
```

### Common commands

```bash
# List all apps and their health
argocd app list

# Get details + sync status for one app
argocd app get argocd/<app-name>

# Trigger a sync
argocd app sync argocd/<app-name>

# Sync a specific resource within an app
argocd app sync argocd/<app-name> --resource batch:Job:<job-name>

# Force a hard refresh (re-evaluates git)
argocd app get argocd/<app-name> --refresh
```

### Sync waves

Resources deploy in this order:

| Wave | Contents |
|---|---|
| `1` | Infrastructure (operators, cert-manager, gateway, sealed-secrets) |
| `2` | Applications (gzac-backend, openzaak, opennotificaties, keycloak, …) |

If an operator is not yet healthy, dependent applications will wait and retry (up to 10 times as configured in the ApplicationSet retry policy).
