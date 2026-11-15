
const frames = [
  {
    id: 1,
    time: '10:30:01',
    objects: 'Door, Wall',
    ocr: 'Computer Laboratory',
  },
  {
    id: 2,
    time: '10:30:05',
    objects: 'Chair, Table',
    ocr: 'None',
  },
  {
    id: 3,
    time: '10:30:10',
    objects: 'Door',
    ocr: 'Exit',
  },
];

const questions = [
  {
    question: 'What is in front of me?',
    answer: 'There is a door in front of you.',
  },
  {
    question: 'What was written on the door?',
    answer: 'Computer Laboratory.',
  },
];

function App() {
  return (
    <div className="dashboard">
      <header className="header">
        <div>
          <h1>World Voice AI</h1>
          <p>Multimedia Monitoring Dashboard</p>
        </div>

        <span className="demo-badge">DEMO MODE</span>
      </header>

      <main>
        <section className="stats">
          <div className="card">
            <h3>Backend Connection</h3>
            <p className="warning">Not Connected</p>
          </div>

          <div className="card">
            <h3>Stored Frames</h3>
            <p>3 Sample Frames</p>
          </div>

          <div className="card">
            <h3>AI Questions</h3>
            <p>2 Sample Questions</p>
          </div>
        </section>

        <section className="card live-section">
          <h2>Live Camera Feed</h2>

          <div className="video-placeholder">
            <span>📷</span>
            <p>Waiting for camera connection</p>
            <small>Live streaming is not connected yet.</small>
          </div>
        </section>

        <section className="card">
          <h2>Captured Frames Timeline</h2>

          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Frame ID</th>
                  <th>Timestamp</th>
                  <th>Detected Objects</th>
                  <th>OCR Text</th>
                </tr>
              </thead>

              <tbody>
                {frames.map((frame) => (
                  <tr key={frame.id}>
                    <td>{frame.id}</td>
                    <td>{frame.time}</td>
                    <td>{frame.objects}</td>
                    <td>{frame.ocr}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="card">
          <h2>Previous Questions & Answers</h2>

          {questions.map((item, index) => (
            <div className="question-item" key={index}>
              <p className="question">Q: {item.question}</p>
              <p className="answer">A: {item.answer}</p>
            </div>
          ))}
        </section>
      </main>

      <footer>
        World Voice AI — University Multimedia Project
      </footer>
    </div>
  );
}

export default App;
