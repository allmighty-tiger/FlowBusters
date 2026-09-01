import { useNavigate } from 'react-router-dom';

const PARAGRAPHS: string[] = [
  'It was the year of our Captain, 1719, when a small crew of very serious software engineers decided that locks on the web are mostly for show. "The lock," said the Captain, tapping a door that had never once been a door, "was only painted."',
  'So they sailed the FlowShop, the most trusting little shop in all the sea, where a customer could order a hat, approve his own hat, and then — because the shop kept no careful ledger — ship the hat to any address he pleased. The shopkeeper had built it for friends. It was not, as it turned out, ready for the Captain.',
  'The Recorder, a quiet fellow who spoke mostly in HTTP, walked the shop end to end and wrote every request down in a leather book. "We have captured the flow," he reported, "including the part where the shop asks the hat who it is, and the hat says who it is, and the shop believes it."',
  'The Analyst spread the leather book across the table and drew a map of the ship\'s hold: which crates could only be opened by the foreman, which by the captain, which by no one. "There," she pointed, to a crate marked APPROVAL. "This one is guarded only by a piece of paper the customer hands you himself."',
  'The Saboteur did not sleep for three nights. He built five cursed little scripts: one to skip the foreman\'s signature, one to trade one customer\'s paper for another\'s, one to write a new price on the hat — a negative price, so the shop would pay you to take it — one to replay a good request a hundred times, and one to simply walk straight into the captain\'s cabin.',
  'The Prober set the scripts loose. The hat was approved without the foreman. The hat was approved by a man who was not the owner. The hat was sold for negative coins, and the shop, bless its foolish heart, sent a carriage to deliver it. The cabin door opened at a knock of one. Five scripts. Five open holds. The shopkeeper fainted into the cargo net, and woke to find a single sheet of parchment beside him: a fix for each and every one, written in a hand far too neat to be a pirate\'s.',
  '"A lock that only the honest obey," the Captain wrote, "is not a lock at all. It is a suggestion. And we, being pirates, do not honor suggestions."',
];

export default function TallTalePage() {
  const navigate = useNavigate();
  return (
    <div>
      <button onClick={() => navigate('/')} style={{
        background: 'transparent', color: '#e6c15a', border: '1px solid #333',
        padding: '0.3rem 1rem', borderRadius: 4, cursor: 'pointer', fontSize: '0.85rem', marginBottom: '1rem',
      }}>← Back</button>

      <h1 style={{ fontSize: '1.5rem', margin: '0 0 0.4rem', color: '#e6c15a' }}>🏴‍☠️ A Tall Tale of the FlowShop</h1>
      <p style={{ color: '#64748b', fontStyle: 'italic', fontSize: '0.85rem', margin: '0 0 1.5rem' }}>
        As recorded in the ship's log. Names changed to avoid embarrassment. The shop was not embarrassed enough to change them.
      </p>

      <div style={{ display: 'grid', gap: '0.9rem' }}>
        {PARAGRAPHS.map((p, i) => (
          <p key={i} style={{
            color: '#cbd5e1', lineHeight: 1.8, fontSize: '0.98rem',
            background: i % 2 ? 'transparent' : 'none', margin: 0,
          }}>{p}</p>
        ))}
      </div>

      <p style={{
        marginTop: '2rem', textAlign: 'center', color: '#e6c15a',
        fontSize: '1rem', fontWeight: 600, letterSpacing: '0.03em',
      }}>— And that is why we bust workflows. —</p>
    </div>
  );
}