import psycopg
conn = psycopg.connect('postgresql://portforge:portforge@127.0.0.1:55432/portforge')
curr = conn.cursor()
curr.execute('''
INSERT INTO hosts (id, hostname, operating_system, first_seen, last_seen, status, docker_available) 
VALUES ('9a224a91-450e-4078-a400-8cb312dcfdb6', 'lenovoserver', 'Linux', NOW(), NOW(), 'online', True)
ON CONFLICT (id) DO NOTHING;
''')
conn.commit()
print('Inserted lenovoserver into db')
