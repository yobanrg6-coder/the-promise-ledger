/**
 * TopicAhead - Mission Control Frontend Controller
 * Manages Opportunity Radar visualization, Critic Revision Loops, and Real-Time Event Streaming.
 */

let currentCampaignData = null;
let timerInterval = null;
let startTime = 0;
let revisionHistory = {}; // draft_number -> { script, audit }
// Tracks the in-flight /api/generate-stream request, if any. Without this, a
// scan abandoned via Reset Session (or superseded by a new one) keeps
// streaming in the background and its 'complete'/'decision_stop' event still
// fires later, silently repainting stale results over whatever the user
// reset to or started next - observed live: a Reset Session mid-scan, then
// the old ACT_NOW result popped back into the output panel seconds later.
let activeRunController = null;

// Every value rendered through innerHTML below can ultimately originate from
// user input (the niche/region fields, or a direct API call bypassing the
// UI's <select> constraints) or from Gemini's generated text - neither is
// sanitized upstream. Escape before interpolating into any innerHTML template.
function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function setTopic(text) {
    document.getElementById('topicInput').value = text;
}

// Captured once at load, before any scan can overwrite #emptyState's innerHTML
// with a decision_stop/no_data render - resetSession() needs the original
// markup to restore, not whatever the last scan left behind.
const EMPTY_STATE_DEFAULT_HTML = document.getElementById('emptyState').innerHTML;

// Brand Memory Bank's default values (Founders/Executives, Direct & Analytical)
// match the pre-filled AI Agents niche example - switching to an unrelated
// niche (e.g. pet content) without updating them produces a nonsensical mixed
// audience in the LLM's own reasoning ("relevance for founders... centered on
// dog content"). Clearing them to blank forces a conscious refill instead of
// silently carrying over a mismatched default.
function resetSession() {
    if (activeRunController) {
        activeRunController.abort();
        activeRunController = null;
    }

    document.getElementById('topicInput').value = '';
    document.getElementById('brandToneInput').value = '';
    document.getElementById('brandAudienceInput').value = '';
    document.getElementById('winningHooksInput').value = '';
    document.getElementById('apiKeyInput').value = '';

    const emptyState = document.getElementById('emptyState');
    emptyState.innerHTML = EMPTY_STATE_DEFAULT_HTML;
    emptyState.classList.remove('hidden');
    document.getElementById('outputContainer').classList.add('hidden');
    document.getElementById('agentTimeline').classList.add('hidden');
    document.getElementById('agentLiveTerminal').innerHTML = '';

    clearInterval(timerInterval);
    currentCampaignData = null;
    revisionHistory = {};

    // The aborted run's own runCampaign() no longer resets this (its finally
    // block only touches shared UI state when it still owns
    // activeRunController, which we just cleared above) - reset it here
    // instead so a mid-scan Reset Session doesn't leave the button stuck
    // showing "Scanning Radar & Agents...".
    const submitBtn = document.getElementById('submitBtn');
    submitBtn.disabled = false;
    submitBtn.innerHTML = `<span class="btn-text">Scan Radar & Run Agents</span>`;

    document.getElementById('topicInput').focus();
}

function switchTab(evt, tabId) {
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));

    evt.currentTarget.classList.add('active');
    const target = document.getElementById(tabId);
    if (target) {
        target.classList.add('active');
    }
}

function copyText(elementId) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const textToCopy = el.innerText || el.textContent;
    navigator.clipboard.writeText(textToCopy).then(() => {
        alert("Copied to clipboard!");
    }).catch(err => {
        console.error("Copy failed: ", err);
    });
}

function addTerminalLog(agent, message, isCritic = false) {
    const terminal = document.getElementById('agentLiveTerminal');
    if (!terminal) return;

    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    const line = document.createElement('div');
    line.className = 'terminal-line';
    line.innerHTML = `
        <span class="t-time">[${elapsed}s]</span>
        <span class="${isCritic ? 't-critic' : 't-agent'}">${escapeHtml(agent)}:</span>
        <span>${escapeHtml(message)}</span>
    `;
    terminal.appendChild(line);
    terminal.scrollTop = terminal.scrollHeight;
}

document.getElementById('campaignForm').addEventListener('submit', function (e) {
    e.preventDefault();
    const topic = document.getElementById('topicInput').value.trim();
    if (!topic) {
        alert("Please enter a niche or target.");
        return;
    }
    runCampaign(null);
});

// forcedTopic: when set, re-runs the same scan but locks the winning
// opportunity onto a specific alternative the user picked from a previous
// scan's "Trend Signals Evaluated" list, instead of the auto-selected one.
async function runCampaign(forcedTopic) {
    const topic = document.getElementById('topicInput').value.trim();
    const platform = document.getElementById('platformSelect').value;
    const geo = document.getElementById('geoSelect').value;
    const brandTone = document.getElementById('brandToneInput').value.trim();
    const brandAudience = document.getElementById('brandAudienceInput').value.trim();
    const apiKey = document.getElementById('apiKeyInput').value.trim();

    // Supersede whatever scan (if any) was already streaming - only one
    // scan's events should ever be able to paint the UI at a time.
    if (activeRunController) activeRunController.abort();
    const controller = new AbortController();
    activeRunController = controller;

    // UI State: Running
    const submitBtn = document.getElementById('submitBtn');
    submitBtn.disabled = true;
    submitBtn.innerHTML = `<span class="pulse-recording"></span> Scanning Radar & Agents...`;

    document.getElementById('emptyState').classList.add('hidden');
    document.getElementById('outputContainer').classList.add('hidden');
    const timeline = document.getElementById('agentTimeline');
    timeline.classList.remove('hidden');

    const terminal = document.getElementById('agentLiveTerminal');
    terminal.innerHTML = '';

    // Start Timer
    startTime = Date.now();
    const timerDisplay = document.getElementById('overallTimer');
    clearInterval(timerInterval);
    timerInterval = setInterval(() => {
        const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
        timerDisplay.innerText = `${elapsed}s`;
    }, 100);

    addTerminalLog("MissionControl", forcedTopic
        ? `Re-analyzing your pick '${forcedTopic}' for '${topic}' [Region: ${geo}]...`
        : `Starting signal scan for '${topic}' [Region: ${geo}]...`);
    revisionHistory = {};

    try {
        const response = await fetch('/api/generate-stream', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                topic: topic,
                platform: platform,
                geo: geo,
                tone: brandTone,
                target_audience: brandAudience,
                api_key: apiKey || null,
                forced_topic: forcedTopic || null
            }),
            signal: controller.signal
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';

        while (true) {
            if (controller.signal.aborted) return;
            const { value, done } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n\n');
            buffer = lines.pop();

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const jsonStr = line.replace('data: ', '').trim();
                    if (!jsonStr) continue;

                    try {
                        const event = JSON.parse(jsonStr);
                        handleAgentEvent(event);
                    } catch (err) {
                        console.warn("JSON parse error:", err, jsonStr);
                    }
                }
            }
        }

    } catch (error) {
        if (error.name === 'AbortError') {
            return; // superseded by a newer scan or Reset Session - not a real failure
        }
        console.error("Fetch stream error:", error);
        addTerminalLog("Error", `Swarm failure: ${error.message}`, true);
        alert(`Error: ${error.message}`);
    } finally {
        // A superseded run's own finally still fires on abort - only the run
        // that currently owns activeRunController should touch shared UI
        // state (button/timer), or an old run finishing late could stomp on
        // whatever the newer run or Reset Session already set up.
        if (activeRunController === controller) {
            clearInterval(timerInterval);
            submitBtn.disabled = false;
            submitBtn.innerHTML = `<span class="btn-text">Scan New Target</span>`;
        }
    }
}

function handleAgentEvent(event) {
    if (event.type === 'status') {
        const isCritic = event.agent === 'ViralityAuditorAgent';
        addTerminalLog(event.agent, event.message, isCritic);

        if (event.draft) {
            document.getElementById('loopCounter').innerText = `Draft #${event.draft}`;
        }
    } else if (event.type === 'agent_result') {
        if (event.agent === 'TrendScoutAgent') {
            const data = event.data;
            addTerminalLog("OpportunityEngine", `Winning Opportunity: "${data.selected_opportunity}" | Score: ${data.opportunity_radar.total_opportunity_score}/100 [Action: ${data.opportunity_radar.recommended_action}]`);
        } else if (event.agent === 'ScriptHookAgent') {
            const draft = event.data.draft_number;
            revisionHistory[draft] = revisionHistory[draft] || {};
            revisionHistory[draft].script = event.data;
        } else if (event.agent === 'ViralityAuditorAgent') {
            const audit = event.data;
            const draft = audit.draft_evaluated;
            revisionHistory[draft] = revisionHistory[draft] || {};
            revisionHistory[draft].audit = audit;
            addTerminalLog("CriticAgent", `Draft #${audit.draft_evaluated} evaluation: Score ${audit.overall_virality_score}/100 -> ${audit.status}`, true);
        }
    } else if (event.type === 'complete') {
        addTerminalLog("System", "Campaign & Radar Completed Successfully.");
        currentCampaignData = event.payload;
        renderCompleteCampaign(event.payload);
    } else if (event.type === 'decision_stop') {
        addTerminalLog("AttentionEngine", event.message, true);
        renderDecisionStop(event.data);
    } else if (event.type === 'no_data') {
        addTerminalLog("AttentionEngine", event.message, true);
        renderNoData(event.message, event.data);
    } else if (event.type === 'error') {
        addTerminalLog("Error", event.message, true);
    }
}

function renderDecisionStop(data) {
    const trends = data.trend_intelligence;
    const radar = trends.opportunity_radar;
    const action = actionMeta(data.recommended_action);
    const stage = stageMeta(radar.lifecycle_stage);

    const gapsHtml = (trends.cross_market_gaps && trends.cross_market_gaps.length > 0)
        ? `<div class="gaps-box" style="text-align:left; margin-top:1.25rem; max-width:560px;">
             <h4>Get there before your market does</h4>
             <p style="color:var(--text-muted); font-size:0.85rem; margin-top:-0.25rem;">These are already real trends elsewhere — not yet visible in ${escapeHtml(trends.target_market_geo)}. Nothing to react to yet, so nobody else is covering it either.</p>
             ${trends.cross_market_gaps.map(gap => `
                <div class="gap-row">
                    <div>
                        <span class="gap-topic">${escapeHtml(gap.topic)}</span>
                        <span style="color:var(--text-muted);"> — trending in ${escapeHtml(gap.baseline_geo)}, not yet visible in ${escapeHtml(gap.target_geo || trends.target_market_geo)}</span>
                    </div>
                    <div style="display:flex; align-items:center; gap:0.5rem;">
                        <span class="gap-meta">#${gap.baseline_rank} · ${escapeHtml(gap.baseline_search_volume)}</span>
                        <button type="button" class="regenerate-btn" data-topic="${escapeHtml(gap.topic)}">Generate this instead</button>
                    </div>
                </div>
             `).join('')}
           </div>`
        : '';

    const emptyState = document.getElementById('emptyState');
    emptyState.classList.remove('hidden');
    document.getElementById('outputContainer').classList.add('hidden');
    emptyState.innerHTML = `
        <div class="empty-icon">⏸️</div>
        <h2>Verdict: "${escapeHtml(trends.selected_opportunity)}"</h2>
        <div class="radar-badges" style="justify-content:center; margin-bottom:1rem;">
            <div class="lifecycle-badge ${stage.cssClass}">${stage.icon} ${escapeHtml(radar.lifecycle_stage)}</div>
            <div class="action-badge ${action.cssClass}">${action.icon} ${action.label}</div>
        </div>
        <p>The attention engine analyzed the opportunity and decided not to generate content this time.</p>
        <p style="color:var(--text-muted); font-size:0.9rem;">${escapeHtml(data.reason)}</p>
        ${gapsHtml}
        ${(trends.signals_evaluated && trends.signals_evaluated.length > 0) ? `
        <div style="text-align:left; margin-top:1.25rem; max-width:560px;">
            <h4>Trend Signals Evaluated by the Swarm:</h4>
            <div id="decisionSignalsTable" class="signals-table-box"></div>
        </div>` : ''}
    `;

    renderSignalsTable(trends.signals_evaluated, 'decisionSignalsTable');
}

function renderNoData(message, data) {
    const emptyState = document.getElementById('emptyState');
    emptyState.classList.remove('hidden');
    document.getElementById('outputContainer').classList.add('hidden');
    emptyState.innerHTML = `
        <div class="empty-icon">⚠️</div>
        <h2>No live data for this region</h2>
        <p>${escapeHtml(message)}</p>
        <p style="color:var(--text-muted); font-size:0.9rem;">${escapeHtml(data.reason)}</p>
    `;
}

function verdictBadgeClass(verdict) {
    if (verdict === 'SELECTED') return 'verdict-selected';
    if (verdict === 'BACKLOG') return 'verdict-backlog';
    return 'verdict-discarded';
}

// Shared by the ACT_NOW output view and the MONITOR/IGNORE decision_stop
// view - lets the user generate a script for any relevant ('BACKLOG')
// alternative the swarm considered but didn't pick, instead of only ever
// seeing the single auto-selected winner.
function renderSignalsTable(signals, containerId = 'signalsTable') {
    const signalsTable = document.getElementById(containerId);
    if (!signalsTable) return;
    signalsTable.innerHTML = '';
    (signals || []).forEach(sig => {
        const row = document.createElement('div');
        row.className = 'signal-row';
        const showRegenerate = sig.verdict === 'BACKLOG';
        row.innerHTML = `
            <div>
                <strong>${escapeHtml(sig.query_or_topic)}</strong>
                <small style="display:block; color:var(--text-muted);">${escapeHtml(sig.reasoning)}</small>
            </div>
            <div style="display:flex; align-items:center; gap:0.5rem;">
                <span style="font-family:'JetBrains Mono'; font-weight:700;">${sig.opportunity_score}/100</span>
                <span class="verdict-badge ${verdictBadgeClass(sig.verdict)}">${escapeHtml(sig.verdict)}</span>
                ${showRegenerate ? `<button type="button" class="regenerate-btn" data-topic="${escapeHtml(sig.query_or_topic)}">Generate this instead</button>` : ''}
            </div>
        `;
        signalsTable.appendChild(row);
    });
}

// Delegated listener (survives signalsTable.innerHTML being rebuilt on every render).
document.addEventListener('click', function (e) {
    const btn = e.target.closest('.regenerate-btn');
    if (!btn) return;
    btn.disabled = true;
    runCampaign(btn.dataset.topic);
});

function renderCompleteCampaign(payload) {
    const output = document.getElementById('outputContainer');
    output.classList.remove('hidden');

    const trends = payload.trend_intelligence;
    const radar = trends.opportunity_radar;
    const script = payload.script_and_content;
    const visual = payload.visual_and_metadata;
    const audit = payload.virality_audit;

    // Render Opportunity Radar Banner
    document.getElementById('radarTopicTitle').innerText = `"${trends.selected_opportunity}"`;
    document.getElementById('radarVelocity').innerText = radar.search_velocity_percentage || '+420%';
    // saturation_score (0-15, see SATURATION_MAX in agents/scoring.py) is
    // inverted: higher score = LESS saturated. Convert to an actual
    // saturation percentage so "LOW" reads with a low %, not a high one.
    const saturationPct = Math.round((1 - radar.saturation_score / 15) * 100);
    document.getElementById('radarSaturation').innerText = `${radar.content_saturation_level} (${saturationPct}%)`;
    document.getElementById('radarWindow').innerText = radar.estimated_viral_window_hours || '8-12 hours';
    document.getElementById('radarTotalScore').innerText = radar.total_opportunity_score;
    document.getElementById('criticApprovalStatus').innerText = `✓ Approved on Draft #${payload.total_revision_cycles || 1}`;

    const action = actionMeta(radar.recommended_action);
    const actionBadge = document.getElementById('radarActionBadge');
    actionBadge.className = `action-badge ${action.cssClass}`;
    actionBadge.innerText = `${action.icon} ${action.label}`;

    const stage = stageMeta(radar.lifecycle_stage);
    const lifecycleBadge = document.getElementById('radarLifecycleBadge');
    lifecycleBadge.className = `lifecycle-badge ${stage.cssClass}`;
    lifecycleBadge.innerText = `${stage.icon} ${radar.lifecycle_stage}`;

    renderCrossMarketGaps('crossMarketGapsList', 'crossMarketGapsBox', trends.cross_market_gaps, trends.target_market_geo);

    // Score Breakdown Accordion
    const breakdownGrid = document.getElementById('scoreBreakdownGrid');
    breakdownGrid.innerHTML = `
        <div class="breakdown-item"><span>Search Velocity:</span><strong>${radar.velocity_score}/25</strong></div>
        <div class="breakdown-item"><span>Freshness / Recency:</span><strong>${radar.recency_score}/20</strong></div>
        <div class="breakdown-item"><span>Audience Match:</span><strong>${radar.audience_relevance_score}/20</strong></div>
        <div class="breakdown-item"><span>Low Saturation:</span><strong>${radar.saturation_score}/15</strong></div>
        <div class="breakdown-item"><span>Hook Potential:</span><strong>${radar.hook_potential_score}/10</strong></div>
        <div class="breakdown-item"><span>Brand Safety:</span><strong>${radar.brand_safety_score}/10</strong></div>
    `;

    // Tab 1: Script & Scenes
    document.getElementById('hookTextDisplay').innerText = `"${script.hook_3s}"`;
    document.getElementById('ctaTextDisplay').innerText = script.call_to_action;

    const scenesContainer = document.getElementById('scenesList');
    scenesContainer.innerHTML = '';
    script.story_scenes.forEach(scene => {
        const item = document.createElement('div');
        item.className = 'scene-item';
        item.innerHTML = `
            <div class="scene-time">${escapeHtml(scene.timestamp_or_slide_num)}</div>
            <div class="scene-script">${escapeHtml(scene.spoken_audio_or_text)}</div>
            <div class="scene-action">${escapeHtml(scene.visual_action)}</div>
        `;
        scenesContainer.appendChild(item);
    });

    // Tab 2: Caption & Hashtags
    document.getElementById('captionTextDisplay').innerText = script.caption;
    renderTagCloud('broadTags', visual.hashtags.broad_reach_tags);
    renderTagCloud('nicheTags', visual.hashtags.niche_targeted_tags);
    renderTagCloud('trendTags', visual.hashtags.trend_specific_tags);

    // Tab 3: Visual Directives
    document.getElementById('coverPromptDisplay').innerText = visual.cover_image_prompt;
    document.getElementById('colorPaletteDisplay').innerText = visual.color_palette_mood;

    // Tab 4: Critic Audit & Signals
    const strengthsUl = document.getElementById('strengthsList');
    strengthsUl.innerHTML = '';
    audit.strengths.forEach(str => {
        const li = document.createElement('li');
        li.innerText = str;
        strengthsUl.appendChild(li);
    });

    const improvementsUl = document.getElementById('improvementsList');
    improvementsUl.innerHTML = '';
    (audit.rejection_reasons && audit.rejection_reasons.length > 0 ? audit.rejection_reasons : ["Self-correction completed successfully"]).forEach(imp => {
        const li = document.createElement('li');
        li.innerText = imp;
        improvementsUl.appendChild(li);
    });

    renderRevisionTimeline();

    renderSignalsTable(trends.signals_evaluated);

    // Scroll into view
    output.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderRevisionTimeline() {
    const container = document.getElementById('revisionTimeline');
    if (!container) return;
    container.innerHTML = '';

    const draftNumbers = Object.keys(revisionHistory).map(Number).sort((a, b) => a - b);
    if (draftNumbers.length === 0) return;

    draftNumbers.forEach(draftNum => {
        const entry = revisionHistory[draftNum];
        const audit = entry.audit;
        const script = entry.script;
        if (!audit) return;

        const approved = audit.status === 'APPROVED';
        const div = document.createElement('div');
        div.className = `rev-entry ${approved ? 'approved' : ''}`;

        const hookLine = script ? `<div class="rev-hook">"${escapeHtml(script.hook_3s)}"</div>` : '';
        const weakLineBlock = (!approved && audit.actionable_revision_instructions)
            ? `<div class="rev-weak-line"><b>Critic's Correction Instruction</b>${escapeHtml(audit.actionable_revision_instructions)}</div>`
            : '';

        div.innerHTML = `
            <div class="rev-entry-head">
                <span class="rev-draft-label">Draft #${draftNum}</span>
                <span class="rev-status ${approved ? 'approved' : 'rejected'}">${approved ? '✓ APPROVED' : '✕ REJECTED'}</span>
            </div>
            ${hookLine}
            <div class="rev-score">Hook ${audit.hook_strength}/100 · Pacing ${audit.retention_pacing}/100 · Value ${audit.value_density}/100 → Total ${audit.overall_virality_score}/100</div>
            ${weakLineBlock}
        `;
        container.appendChild(div);
    });
}

function stageMeta(stage) {
    const stages = {
        EMERGING: { icon: '🌱', cssClass: 'stage-emerging' },
        ACCELERATING: { icon: '📈', cssClass: 'stage-accelerating' },
        BREAKOUT: { icon: '🔥', cssClass: 'stage-breakout' },
        SATURATED: { icon: '🧊', cssClass: 'stage-saturated' },
    };
    return stages[stage] || stages.EMERGING;
}

function actionMeta(action) {
    const actions = {
        ACT_NOW: { icon: '⚡', label: 'ACT NOW', cssClass: 'act-now' },
        MONITOR: { icon: '⏳', label: 'MONITOR', cssClass: 'monitor' },
        IGNORE: { icon: '🚫', label: 'IGNORE', cssClass: 'ignore' },
    };
    return actions[action] || actions.MONITOR;
}

function renderCrossMarketGaps(containerId, boxId, gaps, targetGeo) {
    const box = document.getElementById(boxId);
    const list = document.getElementById(containerId);
    if (!box || !list) return;

    if (!gaps || gaps.length === 0) {
        box.classList.add('hidden');
        return;
    }

    box.classList.remove('hidden');
    list.innerHTML = gaps.map(gap => `
        <div class="gap-row">
            <div>
                <span class="gap-topic">${escapeHtml(gap.topic)}</span>
                <span style="color:var(--text-muted);"> — trending in ${escapeHtml(gap.baseline_geo)}, not yet visible in ${escapeHtml(gap.target_geo || targetGeo)}</span>
            </div>
            <span class="gap-meta">#${gap.baseline_rank} · ${escapeHtml(gap.baseline_search_volume)}</span>
        </div>
    `).join('');
}

function renderTagCloud(containerId, tagsArray) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '';
    (tagsArray || []).forEach(tag => {
        const span = document.createElement('span');
        span.className = 'hash-tag';
        span.innerText = tag.startsWith('#') ? tag : `#${tag}`;
        container.appendChild(span);
    });
}

function exportCampaignJSON() {
    if (!currentCampaignData) return;
    const blob = new Blob([JSON.stringify(currentCampaignData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `topicahead_campaign_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
}

function exportCampaignMarkdown() {
    if (!currentCampaignData) return;
    const data = currentCampaignData;
    const md = `# TopicAhead: Opportunity & Campaign Report
**Opportunity:** ${data.trend_intelligence.selected_opportunity}
**Opportunity Score:** ${data.trend_intelligence.opportunity_radar.total_opportunity_score}/100 (${data.trend_intelligence.opportunity_radar.recommended_action})
**Search Velocity:** ${data.trend_intelligence.opportunity_radar.search_velocity_percentage}
**Platform:** ${data.platform.toUpperCase()}
**Critic Revision Cycles:** ${data.total_revision_cycles} draft(s)

---

## Retention Hook (0-3s)
> "${data.script_and_content.hook_3s}"

---

## Script / Scene Structure
${data.script_and_content.story_scenes.map(s => `### ${s.timestamp_or_slide_num}\n* **Audio / Voiceover:** ${s.spoken_audio_or_text}\n* **Visual:** _${s.visual_action}_\n`).join('\n')}

---

## Call to Action (CTA)
${data.script_and_content.call_to_action}

---

## Ready-to-Post Copy
\`\`\`text
${data.script_and_content.caption}
\`\`\`

---

## Hashtags (3-Tier Cluster)
* **Broad:** ${data.visual_and_metadata.hashtags.broad_reach_tags.join(' ')}
* **Niche:** ${data.visual_and_metadata.hashtags.niche_targeted_tags.join(' ')}
* **Trends:** ${data.visual_and_metadata.hashtags.trend_specific_tags.join(' ')}

---

## Cover Prompt for Imagen 3 / Midjourney
\`\`\`text
${data.visual_and_metadata.cover_image_prompt}
\`\`\`
`;

    const blob = new Blob([md], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `topicahead_campaign_${Date.now()}.md`;
    a.click();
    URL.revokeObjectURL(url);
}
