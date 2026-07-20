const highlights = [
  'A focused React entry point',
  'Responsive starter styles',
  'Development and production scripts',
]

export default function App() {
  return (
    <main className="page-shell">
      <section className="hero" aria-labelledby="hero-title">
        <p className="eyebrow">LunarForge starter</p>
        <h1 id="hero-title">Build something worth launching.</h1>
        <p className="intro">
          Your React and Vite foundation is ready. Replace this page with your
          product, keep the parts that help, and make the rest unmistakably
          yours.
        </p>
        <a className="primary-action" href="#starter-details">
          Explore the starter
        </a>
      </section>

      <section
        className="starter-details"
        id="starter-details"
        aria-labelledby="details-title"
      >
        <div>
          <p className="section-label">Included</p>
          <h2 id="details-title">Small, useful, and ready to change.</h2>
        </div>
        <ul>
          {highlights.map((highlight) => (
            <li key={highlight}>{highlight}</li>
          ))}
        </ul>
      </section>
    </main>
  )
}
