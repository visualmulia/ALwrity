#!/usr/bin/env python3
"""
Master Orchestrator - Ternak Blog Automation
This script runs daily (Monday - Saturday) to:
1. Identify the niche blog of the day.
2. Load the corresponding credentials and configuration.
3. Pop a keyword from the keywords.txt file.
4. Perform web search and call DeepSeek API to write the article in Bahasa Indonesia.
5. Format the article for Decap CMS (Markdown + YAML Frontmatter).
6. Automate Git clone, commit, and push using Personal Access Tokens (PAT).
7. Log the results in publish_log.csv.
"""

import os
import sys
import datetime
import re
import json
import csv
import shutil
import tempfile
import subprocess
from dotenv import load_dotenv

# Setup basic logging to stdout
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Orchestrator")

# Day mapping to niche and domain (Monday = 0, Tuesday = 1, ..., Saturday = 5, Sunday = 6)
NICHE_MAP = {
    0: ("1_monday_ai", "mesinwaktu.web.id"),
    1: ("2_tuesday_setup", "sudutkreatif.web.id"),
    2: ("3_wednesday_plants", "potkota.web.id"),
    3: ("4_thursday_frugal", "hidupfrugal.web.id"),
    4: ("5_friday_travel", "rutelokal.web.id"),
    5: ("6_saturday_pets", "sobatbulu.web.id"),
}

def load_root_env():
    """Load root environment configurations."""
    root_env_path = os.path.join(os.getcwd(), ".env")
    if os.path.exists(root_env_path):
        load_dotenv(root_env_path)
        logger.info("Successfully loaded root .env configuration.")
    else:
        logger.warning(f"Root .env not found at {root_env_path}. Relying on system environment variables.")

def load_niche_env(niche_dir):
    """Load niche-specific environment configurations from github_cms_configs."""
    niche_env_path = os.path.join("github_cms_configs", f"{niche_dir}.env")
    if os.path.exists(niche_env_path):
        # Override is set to True to load specific Git credentials of the day
        load_dotenv(niche_env_path, override=True)
        logger.info(f"Loaded niche configuration from {niche_env_path}")
        return True
    else:
        logger.error(f"Niche configuration file not found at {niche_env_path}")
        return False

def get_keyword_and_update(niche_dir):
    """Read the first keyword from keywords.txt, remove it, and return it."""
    keywords_file = os.path.join("niche_workspaces", niche_dir, "keywords.txt")
    if not os.path.exists(keywords_file):
        raise FileNotFoundError(f"Keywords file not found at {keywords_file}")

    with open(keywords_file, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    if not lines:
        raise ValueError(f"Keywords file is empty for niche {niche_dir}")

    keyword = lines[0]
    remaining_keywords = lines[1:]

    # Write back remaining keywords
    with open(keywords_file, "w", encoding="utf-8") as f:
        for kw in remaining_keywords:
            f.write(kw + "\n")

    logger.info(f"Selected Keyword: '{keyword}' (Updated keywords.txt, remaining: {len(remaining_keywords)})")
    return keyword

def perform_web_search(query):
    """Perform a web search using Tavily or Serper if API keys are configured."""
    tavily_key = os.getenv("TAVILY_API_KEY")
    serper_key = os.getenv("SERPER_API_KEY")
    
    search_context = ""
    
    # 1. Try Tavily Search
    if tavily_key:
        try:
            logger.info("Performing web search using Tavily API...")
            import requests
            response = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": tavily_key, "query": query, "search_depth": "basic"},
                timeout=15
            )
            if response.status_code == 200:
                results = response.json().get("results", [])
                snippets = []
                for idx, r in enumerate(results[:5]):
                    snippets.append(f"Source {idx+1}: {r.get('title')}\nURL: {r.get('url')}\nContent: {r.get('content')}")
                search_context = "\n\n".join(snippets)
                logger.info(f"Tavily search successfully completed with {len(results)} results.")
                return search_context
        except Exception as e:
            logger.warning(f"Tavily search API failed: {e}")
            
    # 2. Try Serper Search (Google Search API)
    if serper_key:
        try:
            logger.info("Performing web search using Serper API...")
            import requests
            headers = {"X-API-KEY": serper_key, "Content-Type": "application/json"}
            response = requests.post(
                "https://google.serper.dev/search",
                headers=headers,
                json={"q": query},
                timeout=15
            )
            if response.status_code == 200:
                results = response.json().get("organic", [])
                snippets = []
                for idx, r in enumerate(results[:5]):
                    snippets.append(f"Source {idx+1}: {r.get('title')}\nURL: {r.get('link')}\nContent: {r.get('snippet')}")
                search_context = "\n\n".join(snippets)
                logger.info(f"Serper search successfully completed with {len(results)} organic results.")
                return search_context
        except Exception as e:
            logger.warning(f"Serper search API failed: {e}")
            
    logger.info("No search API keys configured or search failed. Proceeding without web search grounding.")
    return search_context

def generate_article(keyword, niche_name, domain, search_context):
    """Call DeepSeek API to write the blog article based on the keyword and web search context."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")

    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY is not configured in the environment variables.")

    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url)

    # Prompt configuration
    system_prompt = (
        "Anda adalah Senior Content Writer dan SEO Expert Bahasa Indonesia.\n"
        "Tugas Anda adalah menulis artikel blog yang mendalam, terstruktur rapi, "
        "menarik, dan ramah SEO berdasarkan keyword yang diberikan.\n"
        "Gunakan Bahasa Indonesia yang alami, mengalir, dan informatif.\n"
        "Format respon wajib berupa JSON valid dengan skema berikut:\n"
        "{\n"
        '  "title": "Judul Artikel yang Menarik dan Mengandung Kata Kunci",\n'
        '  "meta_description": "Deskripsi meta (150-160 karakter) untuk Google Search.",\n'
        '  "tags": ["Tag1", "Tag2"],\n'
        '  "content": "Isi artikel lengkap dalam format Markdown (gunakan H2, H3, bullet points, dsb. Jangan menyertakan judul artikel di dalam isi konten ini)."\n'
        "}"
    )

    user_prompt = (
        f"Niche Blog: {niche_name} (Domain: {domain})\n"
        f"Keyword Utama: {keyword}\n\n"
        "Ketentuan penulisan:\n"
        "1. Tulis minimal 800 kata.\n"
        "2. Masukkan kata kunci utama secara alami di dalam konten.\n"
        "3. Gunakan H2 dan H3 untuk membagi topik pembahasan secara logis.\n"
        "4. Fokus pada kegunaan informasi dan AdSense-friendly (CPC tinggi).\n"
        "5. Jangan buat judul H1 di awal isi konten (properti 'content'), cukup tulis langsung pembahasannya.\n"
        "6. Kembalikan respon hanya dalam bentuk JSON valid sesuai skema yang telah ditentukan."
    )

    if search_context:
        user_prompt += f"\n\nBerikut adalah hasil riset pencarian web (Web Search Context) untuk dijadikan bahan referensi faktual:\n{search_context}"

    logger.info("Requesting content generation from DeepSeek API...")
    
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.3
    )

    resp_text = response.choices[0].message.content.strip()
    article_data = json.loads(resp_text)
    
    logger.info("Successfully generated article from DeepSeek.")
    return article_data

def format_decap_cms(article_data):
    """Format article for Decap CMS: parse template and merge YAML frontmatter + content."""
    template_path = os.path.join("niche_workspaces", "decap_frontmatter_template.txt")
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Decap CMS frontmatter template not found at {template_path}")

    with open(template_path, "r", encoding="utf-8") as f:
        template_content = f.read()

    # Get today's date
    today_str = datetime.date.today().isoformat()

    # Replace values in template
    frontmatter = template_content
    frontmatter = frontmatter.replace('{{title}}', article_data["title"])
    frontmatter = frontmatter.replace('{{date}}', today_str)
    frontmatter = frontmatter.replace('{{image_path}}', '') # Featured image is intentionally left empty
    frontmatter = frontmatter.replace('{{meta_description}}', article_data["meta_description"])

    # Handle tags (Decap CMS template expects 2 tags)
    tags = article_data.get("tags", [])
    tag1 = tags[0] if len(tags) > 0 else "Umum"
    tag2 = tags[1] if len(tags) > 1 else "Info"
    frontmatter = frontmatter.replace('{{tag1}}', tag1)
    frontmatter = frontmatter.replace('{{tag2}}', tag2)

    # Combine frontmatter and markdown body content
    full_content = f"{frontmatter}\n{article_data['content']}"
    return full_content

def create_slug(text):
    """Generate a clean URL-safe slug from text."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'\s+', '-', text)
    return text.strip('-')

def get_authenticated_git_url(repo_url, pat):
    """Inject Personal Access Token into GitHub repository URL for authentication."""
    if not pat:
        return repo_url
    if "https://" in repo_url:
        return repo_url.replace("https://", f"https://{pat}@")
    return repo_url

def execute_git_push(formatted_content, keyword, article_title):
    """Automate Git operations: clone, copy article, commit, and push using PAT."""
    repo_url = os.getenv("GITHUB_REPO_URL")
    pat = os.getenv("GITHUB_PAT")
    content_dir = os.getenv("CONTENT_DIR", "content/blog")

    if not repo_url or not pat:
        raise ValueError("GITHUB_REPO_URL or GITHUB_PAT is missing in the environment variables.")

    def run_git_cmd(args, cwd):
        res = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
        if res.returncode != 0:
            raise Exception(f"Git command failed: {' '.join(args)}\nStderr: {res.stderr}")
        return res.stdout

    # Create safe slug filename
    slug = create_slug(keyword)
    filename = f"{slug}.md"

    logger.info("Initializing temporary directory for git operations...")
    with tempfile.TemporaryDirectory() as temp_dir:
        authenticated_url = get_authenticated_git_url(repo_url, pat)
        
        # 1. Clone target repository
        logger.info("Cloning repository from GitHub...")
        run_git_cmd(["git", "clone", authenticated_url, "repo"], temp_dir)
        repo_path = os.path.join(temp_dir, "repo")

        # 2. Check and create content directory
        target_dir = os.path.join(repo_path, content_dir)
        os.makedirs(target_dir, exist_ok=True)

        # 3. Write formatted markdown file
        file_path = os.path.join(target_dir, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(formatted_content)
        logger.info(f"Written formatted article to: {filename}")

        # 4. Set Git Config
        run_git_cmd(["git", "config", "user.name", "Ternak Blog Automation"], repo_path)
        run_git_cmd(["git", "config", "user.email", "automation@ternakblog.com"], repo_path)

        # 5. Git Status Check & Add
        run_git_cmd(["git", "add", "."], repo_path)
        status = run_git_cmd(["git", "status", "--porcelain"], repo_path)
        
        if not status.strip():
            logger.warning("No changes detected. Article might already be published.")
            return False

        # 6. Commit and Push
        logger.info(f"Committing changes: 'Auto-publish: {article_title}'")
        run_git_cmd(["git", "commit", "-m", f"Auto-publish: {article_title}"], repo_path)
        
        logger.info("Pushing changes to GitHub...")
        # Push to origin HEAD to push to whichever branch is checked out (usually main/master)
        run_git_cmd(["git", "push", "origin", "HEAD"], repo_path)
        logger.info("Successfully pushed changes to GitHub.")
        
    return True

def log_publish_result(niche, keyword, status, error_msg=""):
    """Log the result of today's run to publish_log.csv."""
    csv_file = "publish_log.csv"
    headers = ["Tanggal", "Niche", "Keyword", "Status Git Push", "Detail Error"]
    
    file_exists = os.path.exists(csv_file)
    
    with open(csv_file, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(headers)
        
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow([timestamp, niche, keyword, status, error_msg])
    logger.info(f"Logged publish result in {csv_file}")

def main():
    """Main execution orchestrator."""
    logger.info("Starting Daily Ternak Blog Orchestrator...")
    
    # 1. Determine day of the week
    today = datetime.datetime.today().weekday()
    
    # Sunday is 6
    if today == 6:
        logger.info("Today is Sunday. Resting day. No execution.")
        sys.exit(0)
        
    niche_dir, domain = NICHE_MAP.get(today)
    logger.info(f"Today is scheduled for niche: {niche_dir} (Domain: {domain})")

    load_root_env()
    
    # Preserve global GITHUB_PAT (e.g. from GitHub Actions secrets)
    global_pat = os.getenv("GITHUB_PAT")
    
    if not load_niche_env(niche_dir):
        error_msg = f"Failed to load specific configuration for niche: {niche_dir}"
        log_publish_result(niche_dir, "N/A", "FAILED", error_msg)
        sys.exit(1)

    # If the loaded PAT is the placeholder, restore the global PAT
    niche_pat = os.getenv("GITHUB_PAT")
    if not niche_pat or niche_pat == "[personal-access-token]":
        if global_pat and global_pat != "[personal-access-token]":
            os.environ["GITHUB_PAT"] = global_pat
            logger.info("Restored global GITHUB_PAT from environment.")


    keyword = ""
    try:
        # 2. Get and pop the keyword
        keyword = get_keyword_and_update(niche_dir)
        
        # 3. Perform web search context grounding (optional but recommended)
        search_context = perform_web_search(keyword)
        
        # 4. Generate content using DeepSeek API
        article_data = generate_article(keyword, niche_dir, domain, search_context)
        article_title = article_data.get("title", keyword)
        
        # 5. Format for Decap CMS
        formatted_content = format_decap_cms(article_data)
        
        # 6. Push to GitHub
        success = execute_git_push(formatted_content, keyword, article_title)
        
        if success:
            log_publish_result(niche_dir, keyword, "SUCCESS")
            logger.info("Daily auto-publish process completed successfully!")
        else:
            log_publish_result(niche_dir, keyword, "SKIPPED", "No git changes to commit")
            
    except Exception as e:
        logger.exception("An error occurred during daily orchestrator execution:")
        log_publish_result(niche_dir, keyword if keyword else "N/A", "FAILED", str(e))
        sys.exit(1)

if __name__ == "__main__":
    main()
