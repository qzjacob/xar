// Exploration module types — frontier-research sections, fronts, papers, voices.
// Mirror of the backend xar/api/exploration.py shapes.

export interface ExploreSectionCard {
  id: string;
  name: string;
  nameCn: string;
  icon?: string;
  blurb: string;
  blurbCn: string;
  headline: string;
  momentum: number;
  paperCount: number;
  voiceCount: number;
  articleCount: number;
  frontCount: number;
  topFronts: { title: string; maturity: string; momentum: number }[];
  updatedAt?: string | null;
}

export interface ExploreOverview {
  sections: ExploreSectionCard[];
  totals: { fronts?: number; papers?: number; articles?: number; voices?: number };
  updatedAt?: string | null;
}

export interface ExplorePaper {
  arxivId: string;
  title: string;
  url: string;
  authors: string[];
  published?: string | null;
}

export interface ExploreFront {
  id: string;
  title: string;
  summary: string;
  direction: string;
  significance: string;
  maturity: string; // emerging | accelerating | maturing
  horizon: string; // near | mid | long
  momentum: number;
  confidence: number;
  keyTerms: string[];
  keyVoices: string[];
  papers: ExplorePaper[];
}

export interface ExploreVoice {
  author?: string;
  text: string;
  url?: string;
  expert?: boolean;
}

export interface ExploreArticle {
  title: string;
  url: string;
  summary: string;
}

export interface ExploreSectionDetail {
  section: {
    id: string;
    name: string;
    nameCn: string;
    icon?: string;
    blurb: string;
    blurbCn: string;
    headline: string;
    momentum: number;
    paperCount: number;
    voiceCount: number;
    articleCount: number;
    frontCount: number;
    updatedAt?: string | null;
  };
  fronts: ExploreFront[];
  papers: ExplorePaper[];
  articles: ExploreArticle[];
  voices: ExploreVoice[];
}
