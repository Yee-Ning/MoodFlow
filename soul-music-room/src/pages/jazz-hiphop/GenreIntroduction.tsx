import { soundKeywords } from '../../data/jazzHipHopData'

function GenreIntroduction() {
  return (
    <section
      className="jazz-section jazz-introduction"
      id="about-the-sound"
      aria-labelledby="about-the-sound-title"
    >
      <div className="jazz-section__heading">
        <p>02 / THE LANGUAGE</p>
        <h2 id="about-the-sound-title">ABOUT THE SOUND</h2>
      </div>

      <div className="jazz-introduction__layout">
        <p className="jazz-introduction__lead">
          一段爵士独奏被切开、重排，落在并不完全对齐的鼓点上。钢琴保留指尖的温度，
          贝斯把夜色压低，萨克斯从旧唱片的噪点里缓慢浮现。
        </p>

        <div className="jazz-introduction__body">
          <p>
            Jazz HipHop 不是把两种类型简单叠加。它借用 Jazz
            即兴演奏的自由感，以 HipHop 鼓点建立骨架，再通过黑胶采样寻找时间留下的纹理。
          </p>
          <p>
            Lo-fi 的毛边、钢琴与贝斯的暖色、偶尔穿过的萨克斯，共同构成一座凌晨两点仍亮着灯的城市。
          </p>
        </div>
      </div>

      <div className="jazz-keywords" aria-label="Jazz HipHop themes">
        {soundKeywords.map((keyword, index) => (
          <span key={keyword} data-index={String(index + 1).padStart(2, '0')}>
            {keyword}
          </span>
        ))}
      </div>
    </section>
  )
}

export default GenreIntroduction
