<template>
  <div class="about-view">
    <div class="about-container">
      <h1 class="about-title">About OPARLI</h1>

      <div class="about-section">
        <h2>What We Do</h2>
        <p>
          OPARLI is an experimental algorithmic trading platform that uses artificial
          intelligence to analyze financial markets. We're exploring how AI can identify
          patterns and opportunities through automated analysis.
        </p>
      </div>

      <div class="about-section">
        <h2>Our Approach</h2>
        <ul>
          <li><strong>Self-Evolving Multi-Agent System</strong> - AI agents that learn and adapt from market outcomes</li>
          <li><strong>Computer Vision Analysis</strong> - Pattern recognition on charts using advanced visual AI</li>
          <li><strong>Technical Analysis Integration</strong> - Multi-timeframe indicator analysis and trend detection</li>
          <li><strong>Risk Profile Management</strong> - Dynamic position sizing and risk assessment</li>
        </ul>
        <p class="focus-text">
          Our focus is on Spot Gold (XAUUSD), analyzing market behaviour across multiple timeframes.
        </p>
      </div>

      <div class="about-section">
        <h2>Current Status</h2>
        <div class="status-badge">Beta Phase</div>
        <p>
          Active development with simulated trading. Building toward full transparency:
          users will observe agent reasoning, decisions, and outcomes in real-time.
        </p>
      </div>

      <div class="about-section">
        <h2>About the Project</h2>
        <p>
          Created by Jakub, MSc graduate in Data Science from UvA and employee at ING Amsterdam,
          working towards demonstrating how modern Generative AI and Computer Vision techniques
          can be applied to market analysis and execution.
        </p>
        <p class="github-link">
          <a href="https://github.com/QBKFD" target="_blank" rel="noopener noreferrer">My Github Profile</a>
        </p>
      </div>

      <div class="about-section disclaimer">
        <h2>Disclaimer</h2>
        <p>
          This is an experimental research project for educational purposes. Trading involves
          substantial risk of loss. We do not provide investment advice or manage client funds.
        </p>
      </div>
    </div>
  </div>
</template>

<script>
export default {
  name: 'AboutView',
};
</script>

<style scoped>
.about-view {
  min-height: calc(100vh - 60px);
  background: #101014;
  padding: 40px 20px;
}

.about-container {
  max-width: 800px;
  margin: 0 auto;
}

.about-title {
  font-size: 32px;
  font-weight: 600;
  color: #fff;
  margin-bottom: 40px;
  letter-spacing: 1px;
}

.about-section {
  margin-bottom: 32px;
  padding: 24px;
  background: #18181c;
  border-radius: 12px;
  border: 1px solid #2c2c2c;
}

.about-section h2 {
  font-size: 20px;
  font-weight: 600;
  color: #fff;
  margin-bottom: 16px;
}

.about-section p {
  font-size: 16px;
  line-height: 1.7;
  color: #a0a0a0;
}

.about-section ul {
  list-style: none;
  padding: 0;
  margin: 0;
}

.about-section li {
  font-size: 16px;
  color: #a0a0a0;
  padding: 8px 0;
  padding-left: 24px;
  position: relative;
}

.about-section li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 8px;
  height: 8px;
  background: #4a9eff;
  border-radius: 50%;
}

.about-section a {
  color: #4a9eff;
  text-decoration: none;
}

.about-section a:hover {
  text-decoration: underline;
}

.focus-text {
  margin-top: 16px;
  color: #c0c0c0;
  font-style: italic;
}

.status-badge {
  display: inline-block;
  background: linear-gradient(135deg, #4a9eff 0%, #2d7fd3 100%);
  color: #fff;
  padding: 6px 16px;
  border-radius: 20px;
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 12px;
  letter-spacing: 0.5px;
}

.disclaimer {
  border-color: #3d3d3d;
  background: #1a1a1e;
}

.disclaimer h2 {
  color: #888;
}

.disclaimer p {
  color: #777;
  font-size: 14px;
}

.github-link {
  margin-top: 16px;
}
</style>
