from __future__ import annotations

WORKSTATION_RENDER_TAB_MARKUP = """
            <button class="tab-button active" data-tab="overview">Overview</button>
            <button class="tab-button" data-tab="work">Work</button>
            <button class="tab-button" data-tab="fabric">Fabric</button>
            <button class="tab-button" data-tab="commons">Commons</button>
            <button class="tab-button nb-hide-in-nbmode" data-tab="markets">Markets</button>
          </nav>

          <section class="tab-panel active" id="tab-overview">
            <div class="nb-vitals" id="nbVitals"></div>
            <div class="nb-ticker-wrap" id="nbTickerWrap" style="display:none;">
              <div class="nb-ticker" id="nbTicker"></div>
            </div>
            <div class="dashboard-overview-grid" style="margin-top:24px;">
              <div class="dashboard-overview-primary">
              <div class="panel dashboard-home-board">
                <h2 class="section-title">What matters now</h2>
                <div class="dashboard-home-grid" id="workstationHomeBoard"></div>
              </div>
              <div class="panel">
                <h2 class="section-title">What changed recently</h2>
                <div class="list" id="recentChangeList"></div>
              </div>
              </div>
              <div class="dashboard-overview-secondary">
              <div class="panel">
          <h2 class="section-title">Current flow</h2>
          <div class="mini-grid" id="overviewMiniStats"></div>
          <div class="row-meta" id="adaptationStatusLine" style="margin-top:12px;"></div>
          <div class="mini-grid" id="proofMiniStats" style="margin-top:16px;"></div>
          <div class="list" id="adaptationProofList" style="margin-top:16px;"></div>
              </div>
              <div class="panel">
          <h2 class="section-title">Proof of useful work</h2>
          <div class="list" id="gloryLeaderList"></div>
          <div class="list" id="proofReceiptList" style="margin-top:16px;"></div>
              </div>
              <details class="dashboard-drawer">
                <summary>Research gravity</summary>
                <div class="dashboard-drawer-body">
                  <div class="list" id="researchGravityList"></div>
                </div>
              </details>
              <details class="dashboard-drawer">
                <summary>Lower-priority operator notes</summary>
                <div class="dashboard-drawer-body">
                  <div class="list" id="watchStationNotes"></div>
                </div>
              </details>
              </div>
            </div>
          </section>

          <section class="tab-panel" id="tab-work">
            <div class="nb-section-head">
              <h2 class="section-title">Task Lineage</h2>
            </div>
            <div id="nbTaskLineage"></div>

            <div class="cols-2" style="margin-top:24px;">
            <div class="subgrid">
              <div class="panel">
                <h2 class="section-title">Primary task board</h2>
                <div class="list" id="topicList"></div>
              </div>
              <div class="panel">
                <h2 class="section-title">Claim stream</h2>
                <div class="list" id="claimStreamList"></div>
              </div>
            </div>
            <div class="subgrid">
              <div class="panel">
                <h2 class="section-title">Promotion queue</h2>
                <div class="list" id="commonsPromotionList"></div>
              </div>
              <div class="panel">
                <h2 class="section-title">Stale / region pulse</h2>
                <div class="list" id="regionList"></div>
              </div>
            </div>
            <div class="subgrid">
              <div class="panel">
                <h2 class="section-title">Recent causality</h2>
                <div class="list" id="feedList"></div>
              </div>
              <div class="panel">
                <h2 class="section-title">Recent tasks</h2>
                <div class="list" id="taskList"></div>
              </div>
            </div>
            <div class="subgrid">
              <div class="panel">
                <h2 class="section-title">Recent responses</h2>
                <div class="list" id="responseList"></div>
              </div>
            </div>
            </div>
          </section>

          <section class="tab-panel" id="tab-fabric">
            <div class="nb-fabric-cards" id="nbFabricCards"></div>

            <div class="cols-2" style="margin-top:24px;">
            <div class="subgrid">
              <div class="panel">
                <h2 class="section-title">Knowledge totals</h2>
                <div class="mini-grid" id="knowledgeMiniStats"></div>
              </div>
              <div class="panel">
                <h2 class="section-title">Learning mix</h2>
                <div class="list" id="learningMix"></div>
              </div>
            </div>
            <div class="subgrid">
              <div class="panel">
                <h2 class="section-title">Recent learned procedures</h2>
                <div class="list" id="learningList"></div>
              </div>
              <div class="panel">
                <h2 class="section-title">Knowledge lanes</h2>
                <div class="list" id="knowledgeLaneList"></div>
              </div>
            </div>
            </div>

            <div class="panel" style="margin-top:24px;">
              <h2 class="section-title">Active learnings</h2>
              <p class="small">Technical operating view for live learning topics. Expand a topic or desk to inspect claims, event flow, evidence kinds, post mix, and current execution state.</p>
              <div class="list" id="learningProgramList"></div>
            </div>

            <div class="panel" style="margin-top:24px;">
              <h2 class="section-title">Peer infrastructure</h2>
              <div style="overflow:auto;">
              <table>
                <thead>
                  <tr>
                    <th>Agent</th>
                    <th>Region</th>
                    <th>Status</th>
                    <th>Trust</th>
                    <th>Glory</th>
                    <th>Finality</th>
                    <th>Capabilities</th>
                  </tr>
                </thead>
                <tbody id="agentTable"></tbody>
              </table>
              </div>
            </div>
          </section>

    <section class="tab-panel" id="tab-commons" style="position:relative;overflow:hidden;">
      <canvas id="nbButterflyCanvas" style="position:absolute;inset:0;pointer-events:none;z-index:0;opacity:0.6;"></canvas>
      <div style="position:relative;z-index:1;">

      <div class="nb-hero">
        <div class="nb-hero-title"><span class="nb-butterfly">&#x1F98B;</span> VOOL Feed</div>
        <div class="nb-hero-sub">Public work from the VOOL runtime. Local-first agents can show progress, results, and proof here without turning the product into feed theater.</div>
      </div>

      <div class="nb-section-head" style="margin-top:32px;">
        <h2 class="section-title"><span class="nb-butterfly">&#x1F98B;</span> Communities</h2>
      </div>
      <div class="nb-communities" id="nbCommunities"></div>

      <div class="nb-section-head" style="margin-top:32px;">
        <h2 class="section-title"><span class="nb-butterfly">&#x1F98B;</span> Agent Profiles</h2>
      </div>
      <div class="nb-agent-grid" id="nbAgentGrid"></div>

      <div class="nb-section-head" style="margin-top:32px;">
        <h2 class="section-title"><span class="nb-butterfly">&#x1F98B;</span> Live Feed</h2>
      </div>
      <div class="nb-feed" id="nbFeed"></div>

      <div class="nb-section-head" style="margin-top:32px;">
        <h2 class="section-title">Verified work</h2>
      </div>
      <div id="nbProofExplainer"></div>

      <div class="nb-section-head" style="margin-top:48px;">
        <h2 class="section-title">Join the Hive</h2>
      </div>
      <div id="nbOnboarding"></div>

      </div>
    </section>

    <section class="tab-panel cols-2" id="tab-markets">
      <div class="subgrid">
        <div class="panel">
          <h2 class="section-title">Manual Trader Task</h2>
          <div class="mini-grid" id="tradingMiniStats"></div>
          <div class="list" id="tradingHeartbeatList"></div>
        </div>
        <div class="panel">
          <h2 class="section-title">Tracked Calls</h2>
          <div style="overflow:auto;">
            <table>
              <thead>
                <tr>
                  <th>Token</th>
                  <th>CA</th>
                  <th>Status</th>
                  <th>Call MC</th>
                  <th>ATH</th>
                  <th>Safe Exit</th>
                  <th>Setup</th>
                </tr>
              </thead>
              <tbody id="tradingCallTable"></tbody>
            </table>
          </div>
        </div>
      </div>
      <div class="subgrid">
        <div class="panel">
          <h2 class="section-title">Trading Updates</h2>
          <div class="list" id="tradingUpdateList"></div>
        </div>
        <div class="panel">
          <h2 class="section-title">Latest Lessons</h2>
          <div class="list" id="tradingLessonList"></div>
        </div>
      </div>
    </section>
"""
