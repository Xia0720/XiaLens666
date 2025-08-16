--
-- PostgreSQL database dump
--

-- Dumped from database version 16.8 (Debian 16.8-1.pgdg120+1)
-- Dumped by pg_dump version 16.9 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO postgres;

--
-- Name: image; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.image (
    id integer NOT NULL,
    image_url character varying(255) NOT NULL,
    story_id integer NOT NULL
);


ALTER TABLE public.image OWNER TO postgres;

--
-- Name: image_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.image_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.image_id_seq OWNER TO postgres;

--
-- Name: image_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.image_id_seq OWNED BY public.image.id;


--
-- Name: story; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.story (
    id integer NOT NULL,
    text text NOT NULL,
    created_at timestamp without time zone
);


ALTER TABLE public.story OWNER TO postgres;

--
-- Name: story_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.story_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.story_id_seq OWNER TO postgres;

--
-- Name: story_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.story_id_seq OWNED BY public.story.id;


--
-- Name: image id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.image ALTER COLUMN id SET DEFAULT nextval('public.image_id_seq'::regclass);


--
-- Name: story id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.story ALTER COLUMN id SET DEFAULT nextval('public.story_id_seq'::regclass);


--
-- Data for Name: alembic_version; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.alembic_version (version_num) FROM stdin;
c54c6a6a57ff
\.


--
-- Data for Name: image; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.image (id, image_url, story_id) FROM stdin;
4	https://res.cloudinary.com/dpr0pl2tf/image/upload/v1755040129/i7gyoyjlqgiaee9d1aj9.jpg	7
5	https://res.cloudinary.com/dpr0pl2tf/image/upload/v1755040174/y6l1pc9dn2gkurtqbyxf.jpg	8
6	https://res.cloudinary.com/dpr0pl2tf/image/upload/v1755040188/dqxwh8pse8lp3hedkkak.jpg	8
7	https://res.cloudinary.com/dpr0pl2tf/image/upload/v1755040290/qq3r8mmiqwro9ciamy5j.jpg	9
8	https://res.cloudinary.com/dpr0pl2tf/image/upload/v1755041610/qfbevvi0dg6wd65cztxl.jpg	10
9	https://res.cloudinary.com/dpr0pl2tf/image/upload/v1755043264/wjskl3mkujjj76qjduas.jpg	11
10	https://res.cloudinary.com/dpr0pl2tf/image/upload/v1755044102/hvk3qwxxhbivfflla1lf.avif	12
\.


--
-- Data for Name: story; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.story (id, text, created_at) FROM stdin;
7	45645464	2025-08-12 23:08:49.206943
8	46767787887885替换大方百搭帆布小白鞋变成必须保证下次不成直播直播直播吃不行不行不行吃不消吃不消成本持续表现出不寻常	2025-08-12 23:09:34.196169
10	凄凄切切吃	2025-08-12 23:33:30.159639
11	哈哈哈哈哈哈哈哈	2025-08-13 00:01:03.999447
12	vvvvvv	2025-08-13 00:15:02.546376
9	7777777777765555	2025-08-12 23:11:30.160509
\.


--
-- Name: image_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.image_id_seq', 10, true);


--
-- Name: story_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.story_id_seq', 12, true);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: image image_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.image
    ADD CONSTRAINT image_pkey PRIMARY KEY (id);


--
-- Name: story story_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.story
    ADD CONSTRAINT story_pkey PRIMARY KEY (id);


--
-- Name: image image_story_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.image
    ADD CONSTRAINT image_story_id_fkey FOREIGN KEY (story_id) REFERENCES public.story(id);


--
-- PostgreSQL database dump complete
--

