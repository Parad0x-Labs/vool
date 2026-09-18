from __future__ import annotations

"""Generic knowledge learning-program card runtime fragment for the workstation dashboard."""

WORKSTATION_LEARNING_PROGRAM_KNOWLEDGE_CARDS_RUNTIME = '''
    function buildGenericKnowledgeBody(learning, memory, mesh, recentLearning) {
      const topClasses = learning.top_problem_classes || [];
      const topTags = learning.top_topic_tags || [];
      const genericOverviewHtml = renderLearningMiniStats([
        ['Learned shards', learning.total_learning_shards || 0],
        ['Local generated', learning.local_generated_shards || 0],
        ['Peer received', learning.peer_received_shards || 0],
        ['Web derived', learning.web_derived_shards || 0],
        ['Mesh rows', memory.mesh_learning_rows || 0],
        ['Knowledge manifests', mesh.knowledge_manifests || 0],
      ].map(([label, value]) => [label, fmtNumber(value)]));

      const genericClassesHtml = `
        <article class="card">
          <h3>Top Problem Classes</h3>
          <div class="row-meta">
            ${topClasses.length ? topClasses.map((row) => chip(`${row.problem_class} ${fmtNumber(row.count || 0)}`)).join('') : '<span class="empty">No problem classes yet.</span>'}
          </div>
        </article>
      `;

      const genericTagsHtml = `
        <article class="card">
          <h3>Top Topic Tags</h3>
          <div class="row-meta">
            ${topTags.length ? topTags.map((row) => chip(`${row.tag} ${fmtNumber(row.count || 0)}`)).join('') : '<span class="empty">No topic tags yet.</span>'}
          </div>
        </article>
      `;

      const genericRecentHtml = `
        <article class="card">
          <h3>Recent Learned Procedures</h3>
          <div class="list">
            ${recentLearning.length ? recentLearning.slice(0, 8).map((row) => `
              <article class="card">
                <h3>${esc(row.problem_class || 'learning')}</h3>
                <p>${esc(row.summary || '')}</p>
                <div class="row-meta">
                  ${chip(row.source_type || 'unknown')}
                  <span>quality ${Number(row.quality_score || 0).toFixed(2)}</span>
                </div>
              </article>
            `).join('') : '<div class="empty">No recent learned procedures yet.</div>'}
          </div>
        </article>
      `;

      return `
        <div class="learning-program-grid">
          <article class="card">
            <h3>Overview</h3>
            ${genericOverviewHtml}
          </article>
          <article class="card">
            <h3>Memory Flow</h3>
            ${renderLearningMiniStats([
              ['Local tasks', fmtNumber(memory.local_task_count || 0)],
              ['Responses', fmtNumber(memory.finalized_response_count || 0)],
              ['Own indexed', fmtNumber(mesh.own_indexed_shards || 0)],
              ['Remote indexed', fmtNumber(mesh.remote_indexed_shards || 0)],
            ])}
          </article>
        </div>
        <div class="learning-program-grid">
          ${genericClassesHtml}
          ${genericTagsHtml}
        </div>
        <div class="learning-program-grid wide">
          ${genericRecentHtml}
        </div>
      `;
    }
'''
