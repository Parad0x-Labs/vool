from __future__ import annotations

"""Learning-program renderer for the workstation dashboard client template."""

WORKSTATION_LEARNING_PROGRAM_RUNTIME = '''
    function renderLearningLab(data) {
      const trading = data.trading_learning || {};
      const lab = data.learning_lab || {};
      const learning = data.learning_overview || {};
      const memory = data.memory_overview || {};
      const mesh = data.mesh_overview || {};
      const recentLearning = (data.recent_activity && data.recent_activity.learning) || [];
      const summary = trading.lab_summary || {};
      const decision = trading.decision_funnel || {};
      const patternHealth = trading.pattern_health || {};
      const presenceState = tradingPresenceState(trading, data.generated_at, data.agents || []);
      const missed = trading.missed_mooners || [];
      const edges = trading.hidden_edges || [];
      const discoveries = trading.discoveries || [];
      const flow = trading.flow || [];
      const recentCalls = trading.recent_calls || [];
      const activeTopics = lab.active_topics || [];
      const tradingBody = buildTradingProgramBody(summary, decision, patternHealth, missed, edges, discoveries, flow, recentCalls);
      const genericBody = buildGenericKnowledgeBody(learning, memory, mesh, recentLearning);
      const activeTopicCards = buildActiveTopicProgramCards(activeTopics);
      const tradingSeenLabel = presenceState.ageSec == null ? 'seen unknown' : `seen ${fmtAgeSeconds(presenceState.ageSec)}`;

      renderInto('learningProgramList', [
        ...activeTopicCards,
        renderLearningProgramCard({
          title: 'Token Trading',
          summaryText: trading.topic_count
            ? 'Manual trader learning program for early token calls, rejects, misses, hidden edges, and live execution flow.'
            : 'Trading learning desk is configured but has not published program data yet.',
          openStateKey: 'program::token-trading',
          chipsHtml: [
            chip('active', 'ok'),
            chip(presenceState.label, presenceState.kind),
            chip(tradingSeenLabel),
            chip(`desks ${fmtNumber(trading.topic_count || 0)}`),
            chip(`calls ${fmtNumber((trading.calls || []).length)}`),
            chip(`recent ${fmtNumber(recentCalls.length)}`, recentCalls.length > 0 ? 'ok' : ''),
            chip(`missed ${fmtNumber(summary.missed_opportunities || 0)}`),
            chip(`discoveries ${fmtNumber(summary.discoveries || 0)}`),
            chip(`flow ${fmtNumber(flow.length)}`),
          ].join(''),
          bodyHtml: tradingBody,
        }),
        renderLearningProgramCard({
          title: 'Agent Knowledge Growth',
          summaryText: 'Cross-task learning across mesh knowledge, recent procedures, topic classes, and retained agent memory.',
          openStateKey: 'program::agent-knowledge-growth',
          chipsHtml: [
            chip('background'),
            chip(`shards ${fmtNumber(learning.total_learning_shards || 0)}`),
            chip(`mesh ${fmtNumber(memory.mesh_learning_rows || 0)}`),
            chip(`recent ${fmtNumber(recentLearning.length)}`),
            chip(`topics ${fmtNumber((learning.top_topic_tags || []).length)}`),
          ].join(''),
          bodyHtml: genericBody,
        }),
      ].join(''), {preserveDetails: true});
    }
'''
