# -*- coding: utf-8 -*-
"""Dựng data/rollup.json — FILE DẪN XUẤT.

Nguồn sự thật vẫn là data/YYYY-MM-DD.json. File này được dựng LẠI HOÀN TOÀN
mỗi sáng từ chúng và KHÔNG BAO GIỜ được sửa tay. Mỗi chuỗi mang một nhãn grain:

  day       số đo của đúng ngày đó            -> cộng dồn được
  window28  ảnh chụp cửa sổ 28 ngày trượt     -> KHÔNG cộng được, chỉ đầu->cuối
  state     chỉ số trạng thái tại thời điểm   -> trung bình / lấy cuối kỳ

Chạy: python3 scripts/build_rollup.py --repo . [--check]
"""
import argparse, datetime, glob, io, json, os, re, sys

def dig(o, path):
    cur = o
    for k in path.split('.'):
        if isinstance(cur, dict) and k in cur: cur = cur[k]
        else: return None
    return cur if isinstance(cur, (int, float)) and cur == cur else None

def pick(o, paths):
    for p in paths:
        v = dig(o, p)
        if v is not None: return v
    return None

# ---- chỉ số theo cửa sổ / trạng thái, mỗi repo một bộ -----------------------
WINDOW = {
 'dxliving-seo-report': [
   ('gsc_clicks','window28','GSC — clicks',['gsc_28d.clicks']),
   ('gsc_impressions','window28','GSC — impressions',['gsc_28d.impressions']),
   ('gsc_ctr_pct','window28','GSC — CTR %',['gsc_28d.ctr_pct']),
   ('gsc_position','window28','GSC — vị trí TB',['gsc_28d.avg_position']),
   ('gsc_queries','window28','GSC — số truy vấn',['gsc_28d.queries_with_impressions']),
   ('ga4_sessions','window28','GA4 — sessions',['ga4_28d.sessions']),
   ('ga4_users','window28','GA4 — users',['ga4_28d.users','ga4_28d.total_users']),
   ('ga4_engagement_pct','window28','GA4 — engagement %',['ga4_28d.engagement_rate_pct']),
   ('ga4_key_events','window28','GA4 — key events',['ga4_28d.key_events']),
   ('ahrefs_refdomains','state','Ahrefs — referring domains',['ahrefs.referring_domains']),
   ('ahrefs_backlinks','state','Ahrefs — backlinks',['ahrefs.backlinks']),
   ('ahrefs_ai_citations','state','Ahrefs — trích dẫn AI',['ahrefs.ai_citations']),
   ('ttfb_mean_ms','state','TTFB trung bình (ms)',['technical_health.response_times.site_mean_ms','technical_health.response_times.articles_avg_ms']),
   ('sitemap_urls','state','Sitemap — số URL',['ai_visibility.readiness.sitemap_url_count']),
 ],
 'dxl-social-dashboard-site': [
   ('fb_views_28d','window28','FB — views (28n)',['facebook.views.v']),
   ('fb_interactions_28d','window28','FB — tương tác (28n)',['facebook.interactions.v']),
   ('fb_visits_28d','window28','FB — lượt ghé (28n)',['facebook.visits.v']),
   ('ig_views_28d','window28','IG — views (28n)',['instagram.views.v']),
   ('ig_reach_28d','window28','IG — reach (28n)',['instagram.reach.v']),
   ('ig_interactions_28d','window28','IG — tương tác (28n)',['instagram.interactions.v']),
   ('yt_views_28d','window28','YouTube — views (28n)',['youtube.views_28d.v']),
   ('li_impressions_28d','window28','LinkedIn — impressions (28n)',['linkedin.impressions.v']),
   ('li_reactions_28d','window28','LinkedIn — reactions (28n)',['linkedin.reactions.v']),
   ('li_page_views_28d','window28','LinkedIn — lượt xem trang (28n)',['linkedin.page_views.v']),
   ('fb_followers','state','FB — followers',['facebook.followers']),
   ('ig_followers','state','IG — followers',['instagram.followers']),
   ('li_followers','state','LinkedIn — followers',['linkedin.followers']),
   ('yt_subscribers','state','YouTube — subscribers',['youtube.subscribers']),
 ],
 'dxl-seo-audit': [
   ('health','state','Health score',['health','health_score']),
   ('crawlability','state','Crawlability',['sub_scores.crawlability']),
   ('performance','state','Performance / TTFB',['sub_scores.performance']),
   ('structured_data','state','Structured data',['sub_scores.structured_data']),
   ('onpage','state','On-page / Meta',['sub_scores.onpage']),
   ('content','state','Content',['sub_scores.content']),
   ('pages_crawled','state','Số trang crawl',['pages.crawled','pages_measured']),
   ('ttfb_mean_ms','state','TTFB trung bình (ms)',['ttfb_ms.all','performance.avg_ttfb_sitewide_ms']),
   ('ttfb_median_ms','state','TTFB trung vị (ms)',['ttfb_ms.median']),
   ('words_total','state','Tổng số từ',['content.words_total']),
   ('sitemap_urls','state','Sitemap — số URL',['sitemap.declared_urls','sitemap_urls']),
 ],
}

# ---- chuỗi theo NGÀY THẬT (chỉ Social có sẵn) -------------------------------
DAILY_SOCIAL = [
 ('fb_views_day','FB — views/ngày','facebook.daily.views','meta'),
 ('fb_visits_day','FB — lượt ghé/ngày','facebook.daily.visits','meta'),
 ('fb_interactions_day','FB — tương tác/ngày','facebook.daily.interactions','meta'),
 ('fb_video3s_day','FB — view 3 giây/ngày','facebook.daily.video_3s','meta'),
 ('ig_views_day','IG — views/ngày','instagram.daily.views','meta'),
 ('ig_reach_day','IG — reach/ngày','instagram.daily.reach','meta'),
 ('ig_interactions_day','IG — tương tác/ngày','instagram.daily.interactions','meta'),
]

def dig_list(o, path):
    cur = o
    for k in path.split('.'):
        if isinstance(cur, dict) and k in cur: cur = cur[k]
        else: return None
    return cur if isinstance(cur, list) else None

def window_start(day, which):
    w = (day.get('windows') or {}).get(which) or ''
    m = re.match(r'(\d{4}-\d{2}-\d{2})', w)
    return datetime.date.fromisoformat(m.group(1)) if m else None

def build(repo_dir, repo_name):
    files = sorted(f for f in glob.glob(os.path.join(repo_dir,'data','*.json'))
                   if not os.path.basename(f).startswith('index')
                   and not os.path.basename(f).startswith('rollup'))
    days = []
    for f in files:
        try: days.append((os.path.basename(f)[:-5], json.load(io.open(f,encoding='utf-8'))))
        except Exception as e: print('  ! bỏ qua %s: %s'%(f,e), file=sys.stderr)
    days.sort(key=lambda x: x[0])

    series = {}
    for key, grain, label, paths in WINDOW.get(repo_name, []):
        pts = [{'d': d, 'v': pick(j, paths)} for d, j in days]
        series[key] = {'grain': grain, 'label': label,
                       'points': [p for p in pts if p['v'] is not None],
                       'measured': sum(1 for p in pts if p['v'] is not None),
                       'of_days': len(pts)}

    if repo_name == 'dxl-social-dashboard-site':
        for key, label, path, wkey in DAILY_SOCIAL:
            acc = {}   # date -> (scan_date, value)   giữ lần đọc MỚI NHẤT
            for scan, j in days:
                ser = dig_list(j, path); st = window_start(j, wkey)
                if not ser or not st: continue
                for i, v in enumerate(ser):
                    if not isinstance(v,(int,float)): continue
                    dt = (st + datetime.timedelta(days=i)).isoformat()
                    if dt not in acc or scan >= acc[dt][0]: acc[dt] = (scan, v)
            pts = [{'d': k, 'v': acc[k][1]} for k in sorted(acc)]
            series[key] = {'grain':'day','label':label,'points':pts,
                           'measured':len(pts),'of_days':len(pts),
                           'restated_note':'Mỗi ngày giữ lần đọc mới nhất; Meta có điều chỉnh số về sau.'}

    scanned = [d for d,_ in days]
    out = {
      'kind':'rollup','derived':True,'site':repo_name,
      'generated_at': datetime.datetime.now().strftime('%Y-%m-%d'),
      'source_of_truth':'data/YYYY-MM-DD.json — bất biến. File này là DẪN XUẤT, dựng lại hoàn toàn mỗi lần chạy, không sửa tay.',
      'grain_legend':{
        'day':'Số đo của đúng ngày đó. CỘNG DỒN ĐƯỢC trong khoảng.',
        'window28':'Ảnh chụp cửa sổ 28 ngày trượt. KHÔNG cộng được (các ngày chồng nhau) — chỉ so đầu kỳ với cuối kỳ và vẽ xu hướng.',
        'state':'Chỉ số trạng thái tại thời điểm quét. Lấy trung bình hoặc giá trị cuối kỳ, không cộng.'},
      'scanned_days': scanned,
      'scanned_count': len(scanned),
      'series': series,
    }
    return out

ap = argparse.ArgumentParser()
ap.add_argument('--repo', required=True); ap.add_argument('--name', required=True)
ap.add_argument('--check', action='store_true')
a = ap.parse_args()
r = build(a.repo, a.name)
p = os.path.join(a.repo,'data','rollup.json')
if not a.check:
    io.open(p,'w',encoding='utf-8').write(json.dumps(r, ensure_ascii=False, indent=1, sort_keys=True)+'\n')
print('%-28s %d ngày quét · %d chuỗi · %d byte' % (a.name, r['scanned_count'], len(r['series']),
      os.path.getsize(p) if os.path.exists(p) else 0))
for k,v in sorted(r['series'].items()):
    flag = '' if v['measured']==v['of_days'] else '   (%d/%d)'%(v['measured'],v['of_days'])
    print('   %-22s %-9s %4d điểm%s' % (k, v['grain'], len(v['points']), flag))
