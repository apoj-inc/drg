// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"crypto/ed25519"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/yaronf/httpsign"
)

const signatureKeyID = "woodpecker-ci-extensions"

type config struct {
	Name string `json:"name"`
	Data string `json:"data"`
}

type extensionRequest struct {
	Repo struct {
		GitHTTPURL string `json:"git_http_url"`
		CloneURL   string `json:"clone_url"`
		FullName   string `json:"full_name"`
	} `json:"repo"`
	Pipeline struct {
		Commit       string   `json:"commit"`
		Branch       string   `json:"branch"`
		ChangedFiles []string `json:"changed_files"`
		Event        string   `json:"event"`
		DeployTo     string   `json:"deploy_to"`
		DeployTask   string   `json:"deploy_task"`
	} `json:"pipeline"`
	Netrc *struct {
		Login    string `json:"login"`
		Password string `json:"password"`
	} `json:"netrc"`
	Configuration []config `json:"configuration"`
}

type secret struct {
	Name   string   `json:"name"`
	Value  string   `json:"value"`
	Images []string `json:"images,omitempty"`
}

type secretResponse struct {
	Secrets []secret `json:"secrets"`
}

type extensionResponse struct {
	Configs []config `json:"configs"`
}

type server struct {
	publicKey           ed25519.PublicKey
	generator           string
	forgeHost           string
	openBao             *openBaoClient
	allowedRepositories []string
}

type openBaoClient struct {
	address  string
	authPath string
	role     string
	http     *http.Client

	mu         sync.Mutex
	cachedToken string
	expiresAt  time.Time
}

type openBaoLoginResponse struct {
	Auth struct {
		ClientToken   string `json:"client_token"`
		LeaseDuration int    `json:"lease_duration"`
	} `json:"auth"`
}

func main() {
	publicKey, err := readPublicKey(os.Getenv("WOODPECKER_EXTENSION_PUBLIC_KEY_FILE"))
	if err != nil {
		log.Fatal(err)
	}

	listen := os.Getenv("WOODPECKER_EXTENSION_LISTEN")
	if listen == "" {
		listen = "0.0.0.0:9010"
	}
	generator := os.Getenv("DRG_WOODPECKER_GENERATOR")
	if generator == "" {
		generator = "/opt/woodpecker-config-extension/generate_woodpecker.py"
	}
	forgeHost := os.Getenv("DRG_FORGEJO_HOST")
	if forgeHost == "" {
		forgeHost = "forge.example.test"
	}
	openBao, err := newOpenBaoClient()
	if err != nil {
		log.Fatal(err)
	}
	allowedRepositories := splitList(os.Getenv("DRG_OPENBAO_ALLOWED_REPOSITORIES"))
	if len(allowedRepositories) == 0 {
		log.Fatal("DRG_OPENBAO_ALLOWED_REPOSITORIES is required")
	}

	service := &server{
		publicKey:           publicKey,
		generator:           generator,
		forgeHost:           forgeHost,
		openBao:             openBao,
		allowedRepositories: allowedRepositories,
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		w.WriteHeader(http.StatusOK)
	})
	mux.HandleFunc("/ciconfig", service.handle)
	mux.HandleFunc("/secrets", service.handleSecrets)
	log.Printf("Woodpecker configuration extension listening on %s", listen)
	log.Fatal(http.ListenAndServe(listen, mux))
}

func (s *server) verifyRequest(w http.ResponseWriter, r *http.Request) ([]byte, bool) {
	r.Body = http.MaxBytesReader(w, r.Body, 8<<20)
	verifier, err := httpsign.NewEd25519Verifier(
		s.publicKey,
		httpsign.NewVerifyConfig(),
		httpsign.Headers("@request-target", "content-digest"),
	)
	if err != nil {
		http.Error(w, "signature verifier unavailable", http.StatusInternalServerError)
		return nil, false
	}
	if err := httpsign.VerifyRequest(signatureKeyID, *verifier, r); err != nil {
		log.Printf("rejected unsigned Woodpecker extension request: %v", err)
		http.Error(w, "invalid signature", http.StatusUnauthorized)
		return nil, false
	}
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "cannot read request", http.StatusBadRequest)
		return nil, false
	}
	return body, true
}

func (s *server) handle(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost || r.URL.Path != "/ciconfig" {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	body, ok := s.verifyRequest(w, r)
	if !ok {
		return
	}

	var request extensionRequest
	if err := json.Unmarshal(body, &request); err != nil {
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}
	response, err := s.generate(request)
	if err != nil {
		log.Printf("configuration generation failed: %v", err)
		http.Error(w, "configuration generation failed", http.StatusBadGateway)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(response)
}

func (s *server) handleSecrets(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	body, ok := s.verifyRequest(w, r)
	if !ok {
		return
	}

	var request extensionRequest
	if err := json.Unmarshal(body, &request); err != nil {
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}
	if !s.repositoryAllowed(request) {
		http.Error(w, "OpenBao access is not enabled for this repository", http.StatusForbidden)
		return
	}
	secretName := "openbao_plan_token"
	if strings.EqualFold(strings.TrimSpace(request.Pipeline.Event), "deployment") {
		secretName = "openbao_deploy_token"
	}
	openBaoToken, err := s.openBao.token(r.Context())
	if err != nil {
		log.Printf("OpenBao certificate login failed: %v", err)
		http.Error(w, "OpenBao authentication failed", http.StatusBadGateway)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(secretResponse{Secrets: []secret{
		{
			Name:   secretName,
			Value:  openBaoToken,
		},
	}})
}

func newOpenBaoClient() (*openBaoClient, error) {
	address := strings.TrimRight(strings.TrimSpace(os.Getenv("DRG_OPENBAO_ADDR")), "/")
	parsed, err := url.Parse(address)
	if err != nil || parsed.Scheme != "https" || parsed.Host == "" {
		return nil, errors.New("DRG_OPENBAO_ADDR must be an HTTPS URL")
	}
	authPath := strings.Trim(os.Getenv("DRG_OPENBAO_AUTH_PATH"), "/")
	role := strings.TrimSpace(os.Getenv("DRG_OPENBAO_CERT_ROLE"))
	caFile := strings.TrimSpace(os.Getenv("DRG_OPENBAO_CA_FILE"))
	certFile := strings.TrimSpace(os.Getenv("DRG_OPENBAO_CERT_FILE"))
	keyFile := strings.TrimSpace(os.Getenv("DRG_OPENBAO_KEY_FILE"))
	if authPath == "" || role == "" || caFile == "" || certFile == "" || keyFile == "" {
		return nil, errors.New("DRG_OPENBAO_AUTH_PATH, DRG_OPENBAO_CERT_ROLE, DRG_OPENBAO_CA_FILE, DRG_OPENBAO_CERT_FILE, and DRG_OPENBAO_KEY_FILE are required")
	}

	caData, err := os.ReadFile(caFile)
	if err != nil {
		return nil, fmt.Errorf("read OpenBao CA file: %w", err)
	}
	roots, err := x509.SystemCertPool()
	if err != nil || roots == nil {
		roots = x509.NewCertPool()
	}
	if !roots.AppendCertsFromPEM(caData) {
		return nil, fmt.Errorf("OpenBao CA file %s does not contain a certificate", caFile)
	}
	if _, err := tls.LoadX509KeyPair(certFile, keyFile); err != nil {
		return nil, fmt.Errorf("load OpenBao client certificate: %w", err)
	}

	return &openBaoClient{
		address:  address,
		authPath: authPath,
		role:     role,
		http: &http.Client{
			Timeout: 20 * time.Second,
			Transport: &http.Transport{
				TLSClientConfig: &tls.Config{
					MinVersion: tls.VersionTLS12,
					RootCAs:    roots,
					GetClientCertificate: func(*tls.CertificateRequestInfo) (*tls.Certificate, error) {
						certificate, err := tls.LoadX509KeyPair(certFile, keyFile)
						if err != nil {
							return nil, fmt.Errorf("load renewed OpenBao client certificate: %w", err)
						}
						return &certificate, nil
					},
				},
			},
		},
	}, nil
}

func (c *openBaoClient) token(ctx context.Context) (string, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.cachedToken != "" && time.Now().Add(time.Minute).Before(c.expiresAt) {
		return c.cachedToken, nil
	}

	requestBody, err := json.Marshal(map[string]string{"name": c.role})
	if err != nil {
		return "", err
	}
	endpoint := c.address + "/v1/auth/" + url.PathEscape(c.authPath) + "/login"
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, strings.NewReader(string(requestBody)))
	if err != nil {
		return "", err
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := c.http.Do(request)
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return "", fmt.Errorf("OpenBao returned HTTP %d", response.StatusCode)
	}
	var login openBaoLoginResponse
	if err := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&login); err != nil {
		return "", fmt.Errorf("decode OpenBao login response: %w", err)
	}
	if login.Auth.ClientToken == "" {
		return "", errors.New("OpenBao login response did not contain a client token")
	}
	lease := time.Duration(login.Auth.LeaseDuration) * time.Second
	if lease <= 0 {
		lease = 5 * time.Minute
	}
	c.cachedToken = login.Auth.ClientToken
	c.expiresAt = time.Now().Add(lease)
	return c.cachedToken, nil
}

func splitList(value string) []string {
	var result []string
	for _, item := range strings.Split(value, ",") {
		item = strings.Trim(strings.TrimSpace(item), "/")
		if item != "" {
			result = append(result, item)
		}
	}
	return result
}

func (s *server) repositoryAllowed(request extensionRequest) bool {
	candidates := []string{request.Repo.FullName}
	for _, cloneURL := range []string{request.Repo.GitHTTPURL, request.Repo.CloneURL} {
		parsed, err := url.Parse(cloneURL)
		if err != nil || !strings.EqualFold(parsed.Hostname(), s.forgeHost) {
			continue
		}
		path := strings.Trim(strings.TrimSuffix(parsed.Path, ".git"), "/")
		candidates = append(candidates, path)
	}
	for _, candidate := range candidates {
		candidate = strings.Trim(strings.TrimSpace(candidate), "/")
		for _, allowed := range s.allowedRepositories {
			if strings.EqualFold(candidate, allowed) {
				return true
			}
		}
	}
	return false
}

func (s *server) generate(request extensionRequest) (extensionResponse, error) {
	if request.Netrc == nil || request.Netrc.Login == "" || request.Netrc.Password == "" {
		return extensionResponse{}, errors.New("Woodpecker netrc credentials were not supplied")
	}
	cloneURL := request.Repo.GitHTTPURL
	if cloneURL == "" {
		cloneURL = request.Repo.CloneURL
	}
	u, err := url.Parse(cloneURL)
	if err != nil || u.Hostname() != s.forgeHost || u.Scheme != "https" {
		return extensionResponse{}, fmt.Errorf("refusing repository URL %q", cloneURL)
	}
	if request.Pipeline.Commit == "" {
		return extensionResponse{}, errors.New("Woodpecker pipeline commit is empty")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Minute)
	defer cancel()
	workDir, err := os.MkdirTemp("", "woodpecker-config-")
	if err != nil {
		return extensionResponse{}, err
	}
	defer os.RemoveAll(workDir)
	checkout := filepath.Join(workDir, "repo")
	ask := func(name string, args ...string) error {
		command := exec.CommandContext(ctx, name, args...)
		command.Env = append(os.Environ(),
			"GIT_TERMINAL_PROMPT=0",
			"GIT_ASKPASS="+filepath.Join(workDir, ".git-askpass"),
			"GIT_ASKPASS_REQUIRE=force",
			"EXTENSION_GIT_USERNAME="+request.Netrc.Login,
			"EXTENSION_GIT_PASSWORD="+request.Netrc.Password,
		)
		output, err := command.CombinedOutput()
		if err != nil {
			return fmt.Errorf("%s: %w: %s", name, err, strings.TrimSpace(string(output)))
		}
		return nil
	}
	askEnv := filepath.Join(workDir, ".git-askpass")
	if err := os.WriteFile(askEnv, []byte("#!/bin/sh\ncase \"$1\" in\n*Username*) printf '%s' \"$EXTENSION_GIT_USERNAME\" ;;\n*) printf '%s' \"$EXTENSION_GIT_PASSWORD\" ;;\nesac\n"), 0700); err != nil {
		return extensionResponse{}, err
	}
	cloneCommand := exec.CommandContext(ctx, "git", "clone", "--no-checkout", "--depth=1", cloneURL, checkout)
	cloneCommand.Env = append(os.Environ(),
		"GIT_TERMINAL_PROMPT=0",
		"GIT_ASKPASS_REQUIRE=force",
		"GIT_ASKPASS="+askEnv,
		"EXTENSION_GIT_USERNAME="+request.Netrc.Login,
		"EXTENSION_GIT_PASSWORD="+request.Netrc.Password,
	)
	if output, err := cloneCommand.CombinedOutput(); err != nil {
		return extensionResponse{}, fmt.Errorf("git clone: %w: %s", err, strings.TrimSpace(string(output)))
	}
	if err := ask("git", "-C", checkout, "fetch", "--depth=1", "origin", request.Pipeline.Commit); err != nil {
		return extensionResponse{}, err
	}
	if err := ask("git", "-C", checkout, "checkout", "--detach", "FETCH_HEAD"); err != nil {
		return extensionResponse{}, err
	}

	changedFileList := request.Pipeline.ChangedFiles
	if strings.EqualFold(strings.TrimSpace(request.Pipeline.Event), "deployment") {
		changedFileList, err = deploymentChangedFiles(ctx, checkout, request.Pipeline.Commit, ask)
		if err != nil {
			return extensionResponse{}, fmt.Errorf("derive deployment changed files: %w", err)
		}
	}

	generatedDir := filepath.Join(checkout, ".woodpecker-generated")
	generator := exec.CommandContext(ctx, "python3", s.generator, "--output-dir", generatedDir)
	changedFiles, err := json.Marshal(changedFileList)
	if err != nil {
		return extensionResponse{}, fmt.Errorf("encode changed files: %w", err)
	}
	// A missing changed_files field must fail closed. Passing JSON null makes
	// generate_woodpecker.py interpret the change set as unknown and select
	// every service, which can create a full infrastructure rollout by mistake.
	if changedFileList == nil {
		changedFiles = []byte("[]")
	}
	generator.Env = append(
		os.Environ(),
		"DRG_REPO_ROOT="+checkout,
		"DRG_CHANGED_FILES="+string(changedFiles),
		"CI_PIPELINE_EVENT="+strings.TrimSpace(request.Pipeline.Event),
		"CI_COMMIT_BRANCH="+strings.TrimSpace(request.Pipeline.Branch),
		"CI_PIPELINE_DEPLOY_TARGET="+strings.TrimSpace(request.Pipeline.DeployTo),
		"CI_PIPELINE_DEPLOY_TASK="+strings.TrimSpace(request.Pipeline.DeployTask),
	)
	if output, err := generator.CombinedOutput(); err != nil {
		return extensionResponse{}, fmt.Errorf("generate_woodpecker.py: %w: %s", err, strings.TrimSpace(string(output)))
	}

	configs := make([]config, 0, len(request.Configuration)+3)
	for _, original := range request.Configuration {
		configs = append(configs, config{Name: workflowName(original.Name), Data: original.Data})
	}
	generated, err := filepath.Glob(filepath.Join(generatedDir, "*.yml"))
	if err != nil {
		return extensionResponse{}, err
	}
	sort.Strings(generated)
	generatedNames := make([]string, 0, len(generated))
	for _, path := range generated {
		data, err := os.ReadFile(path)
		if err != nil {
			return extensionResponse{}, err
		}
		configs = append(configs, config{Name: strings.TrimSuffix(filepath.Base(path), ".yml"), Data: string(data)})
		generatedNames = append(generatedNames, filepath.Base(path))
	}
	log.Printf("generated Woodpecker configs: repository=%s commit=%s event=%s changed_files=%d workflows=%v", request.Repo.FullName, request.Pipeline.Commit, request.Pipeline.Event, len(changedFileList), generatedNames)
	return extensionResponse{Configs: configs}, nil
}

func deploymentChangedFiles(ctx context.Context, checkout, commit string, ask func(string, ...string) error) ([]string, error) {
	show := exec.CommandContext(ctx, "git", "-C", checkout, "show", "-s", "--format=%P", commit)
	output, err := show.Output()
	if err != nil {
		return nil, fmt.Errorf("read commit parents: %w", err)
	}
	parents := strings.Fields(string(output))
	var diff []byte
	if len(parents) == 0 {
		rootDiff := exec.CommandContext(ctx, "git", "-C", checkout, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", commit)
		diff, err = rootDiff.Output()
	} else {
		parent := parents[0]
		if err := ask("git", "-C", checkout, "fetch", "--depth=1", "origin", parent); err != nil {
			return nil, fmt.Errorf("fetch first parent %s: %w", parent, err)
		}
		parentDiff := exec.CommandContext(ctx, "git", "-C", checkout, "diff", "--name-only", parent, commit)
		diff, err = parentDiff.Output()
	}
	if err != nil {
		return nil, fmt.Errorf("read commit diff: %w", err)
	}

	seen := make(map[string]struct{})
	files := make([]string, 0)
	for _, line := range strings.Split(string(diff), "\n") {
		path := strings.TrimSpace(line)
		if path == "" {
			continue
		}
		if _, exists := seen[path]; exists {
			continue
		}
		seen[path] = struct{}{}
		files = append(files, path)
	}
	sort.Strings(files)
	return files, nil
}

func workflowName(name string) string {
	base := filepath.Base(name)
	base = strings.TrimSuffix(base, ".yaml")
	base = strings.TrimSuffix(base, ".yml")
	if base == ".woodpecker" || base == "woodpecker" {
		return "validate"
	}
	return strings.TrimPrefix(base, ".")
}

func readPublicKey(path string) (ed25519.PublicKey, error) {
	if path == "" {
		return nil, errors.New("WOODPECKER_EXTENSION_PUBLIC_KEY_FILE is required")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	block, _ := pem.Decode(data)
	if block == nil {
		return nil, errors.New("Woodpecker extension public key is not PEM")
	}
	key, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		return nil, err
	}
	publicKey, ok := key.(ed25519.PublicKey)
	if !ok {
		return nil, errors.New("Woodpecker extension public key is not Ed25519")
	}
	return publicKey, nil
}
